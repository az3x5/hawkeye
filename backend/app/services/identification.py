"""Identification.

Runs the pipeline that turns a query image into a *proposal* about who it
shows: detect, embed, search, decide. The decision itself is made by
``app.domain.identity``, which knows nothing about storage or models — this
service only supplies it with evidence and records what came out.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import cv2
import numpy as np

from app.connectors.filesystem.object_store import sha256_bytes
from app.domain.audit import SYSTEM_ACTOR, Actor, AuditAction, AuditEvent, AuditLog
from app.domain.identity import (
    DecisionOutcome,
    DecisionThresholds,
    IdentificationStore,
    IdentityDecision,
    ReviewOutcome,
    StoredIdentification,
    decide,
)
from app.domain.jobs import ObjectStore
from app.domain.recognition import FaceEmbedding
from app.domain.repositories import ConflictError
from app.domain.vectors import VectorRepository

logger = logging.getLogger(__name__)


class IdentificationError(Exception):
    """The query could not be turned into a decision."""


@dataclass(frozen=True, slots=True)
class IdentificationResult:
    """A recorded identification."""

    identification_uuid: UUID
    decision: IdentityDecision


class IdentificationService:
    """Identifies a face and records the attempt."""

    def __init__(
        self,
        *,
        vectors: VectorRepository,
        store: IdentificationStore,
        audit: AuditLog,
        thresholds: DecisionThresholds,
        candidate_limit: int,
        objects: ObjectStore | None = None,
        detector: object | None = None,
        recognizer: object | None = None,
    ) -> None:
        """Wire the service to its collaborators and the policy in force.

        Unlike enrolment, identification needs the models in the request path:
        the caller is waiting for an answer, so the work cannot be handed to a
        worker. ``detector`` and ``recognizer`` are optional so callers that
        already hold an embedding need not load them at all.
        """
        self._vectors = vectors
        self._store = store
        self._audit = audit
        self._thresholds = thresholds
        self._candidate_limit = candidate_limit
        self._objects = objects
        self._detector = detector
        self._recognizer = recognizer

    async def embed_query(self, image_bytes: bytes) -> FaceEmbedding:
        """Detect and embed the single face in a query image.

        An image with no face, or with more than one, is refused rather than
        guessed: picking which face to identify is exactly the judgement the
        caller is asking the system to make, and guessing it would hide the
        ambiguity behind a confident-looking answer.
        """
        if self._detector is None or self._recognizer is None:
            raise IdentificationError("identification models are not configured")

        decoded = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        if decoded is None:
            raise IdentificationError("the query image could not be decoded")

        faces = self._detector.detect_and_align(decoded.astype(np.uint8))  # type: ignore[attr-defined]
        if not faces:
            raise IdentificationError("no face was detected in the query image")
        if len(faces) > 1:
            raise IdentificationError(
                f"{len(faces)} faces were detected; identification requires exactly one"
            )
        return self._recognizer.embed(faces[0])  # type: ignore[attr-defined,no-any-return]

    async def identify(
        self, embedding: FaceEmbedding, *, query_bytes: bytes, actor: Actor | None = None
    ) -> IdentificationResult:
        """Search for the face and record the resulting proposal."""
        matches = await self._vectors.search(embedding, limit=self._candidate_limit)
        decision = decide(matches, self._thresholds)

        identification_uuid = uuid4()
        query_sha256 = sha256_bytes(query_bytes)
        # Retain the query so a reviewer can see what was actually submitted.
        # Without it, a review is a judgement about an image nobody can look at.
        if self._objects is not None:
            await self._objects.put(query_sha256, query_bytes)
        await self._store.add(identification_uuid, query_sha256, decision)

        best = decision.best
        await self._audit.record(
            AuditEvent(
                action=AuditAction.IDENTIFICATION_PERFORMED,
                # An automatic proposal is attributed to the system, never to a
                # human, so the log cannot blur who actually judged.
                actor=actor or SYSTEM_ACTOR,
                identification_uuid=identification_uuid,
                person_uuid=best.person_uuid if best else None,
                policy_version=self._thresholds.policy_version,
                details={
                    "outcome": decision.outcome.value,
                    "accept_at": self._thresholds.accept_at,
                    "review_at": self._thresholds.review_at,
                    "best_score": best.score if best else None,
                    "margin": decision.margin,
                    "candidate_count": len(decision.candidates),
                    "query_sha256": query_sha256,
                },
            )
        )
        logger.info(
            "identification performed",
            extra={
                "identification_uuid": str(identification_uuid),
                "outcome": decision.outcome.value,
                "policy_version": self._thresholds.policy_version,
            },
        )
        return IdentificationResult(identification_uuid, decision)

    async def review(
        self,
        identification_uuid: UUID,
        *,
        outcome: ReviewOutcome,
        reviewer: Actor,
        note: str | None = None,
    ) -> StoredIdentification:
        """Record a human's conclusion and return the updated record.

        Returns the record rather than leaving the caller to re-read it: the
        write is not committed until this service's transaction ends, so a read
        through any other session would not see it yet.

        Only proposals that asked for review can be reviewed: rubber-stamping
        an automatic accept, or overturning a reject that nobody was asked
        about, would make the audit trail misleading about what a human
        actually considered.
        """
        if reviewer.kind != "user":
            raise IdentificationError("a review must be attributed to a person, not the system")

        stored = await self._store.get(identification_uuid)
        if stored is None:
            raise IdentificationError(f"no identification {identification_uuid}")
        if stored.decision.outcome is not DecisionOutcome.REVIEW:
            raise IdentificationError(
                f"identification {identification_uuid} was decided "
                f"'{stored.decision.outcome.value}' and was never sent for review"
            )

        reviewed_at = datetime.now(UTC)
        try:
            await self._store.record_review(
                identification_uuid,
                outcome=outcome,
                reviewer=reviewer.identifier,
                note=note,
                reviewed_at=reviewed_at,
            )
        except ConflictError as exc:
            raise IdentificationError(str(exc)) from exc

        best = stored.decision.best
        await self._audit.record(
            AuditEvent(
                action=AuditAction.IDENTIFICATION_REVIEWED,
                actor=reviewer,
                occurred_at=reviewed_at,
                identification_uuid=identification_uuid,
                person_uuid=best.person_uuid if best else None,
                policy_version=stored.decision.thresholds.policy_version,
                details={
                    "review_outcome": outcome.value,
                    "proposed_outcome": stored.decision.outcome.value,
                    "best_score": best.score if best else None,
                    "note": note,
                },
            )
        )
        logger.info(
            "identification reviewed",
            extra={
                "identification_uuid": str(identification_uuid),
                "review_outcome": outcome.value,
                "reviewer": reviewer.identifier,
            },
        )
        reviewed = await self._store.get(identification_uuid)
        if reviewed is None:  # pragma: no cover - the row was just updated
            raise IdentificationError(f"identification {identification_uuid} vanished mid-review")
        return reviewed
