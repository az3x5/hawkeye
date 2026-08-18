"""Append-only audit log and identification records over PostgreSQL."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.postgres.tables import audit_events, identifications
from app.domain.audit import Actor, AuditAction, AuditEvent
from app.domain.identity import (
    Candidate,
    DecisionOutcome,
    DecisionThresholds,
    IdentityDecision,
    ReviewOutcome,
    StoredIdentification,
)
from app.domain.repositories import ConflictError


class SqlAlchemyAuditLog:
    """``AuditLog`` over the ``audit_events`` table.

    Exposes append and read only. There is no update or delete method, so the
    log cannot be rewritten through this interface.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Bind the log to an open session."""
        self._session = session

    async def record(self, event: AuditEvent) -> AuditEvent:
        """Append an event."""
        await self._session.execute(
            audit_events.insert().values(
                audit_uuid=event.audit_uuid,
                occurred_at=event.occurred_at,
                action=event.action.value,
                actor_identifier=event.actor.identifier,
                actor_kind=event.actor.kind,
                person_uuid=event.person_uuid,
                face_sample_uuid=event.face_sample_uuid,
                identification_uuid=event.identification_uuid,
                policy_version=event.policy_version,
                details=event.details,
            )
        )
        return event

    async def for_person(self, person_uuid: UUID, *, limit: int = 100) -> Sequence[AuditEvent]:
        """Return the events touching a person, most recent first."""
        result = await self._session.execute(
            select(audit_events)
            .where(audit_events.c.person_uuid == person_uuid)
            .order_by(audit_events.c.occurred_at.desc())
            .limit(limit)
        )
        return [self._to_event(row) for row in result.all()]

    async def for_identification(self, identification_uuid: UUID) -> Sequence[AuditEvent]:
        """Return the events belonging to one identification, oldest first."""
        result = await self._session.execute(
            select(audit_events)
            .where(audit_events.c.identification_uuid == identification_uuid)
            .order_by(audit_events.c.occurred_at)
        )
        return [self._to_event(row) for row in result.all()]

    @staticmethod
    def _to_event(row: object) -> AuditEvent:
        return AuditEvent(
            audit_uuid=row.audit_uuid,  # type: ignore[attr-defined]
            occurred_at=row.occurred_at,  # type: ignore[attr-defined]
            action=AuditAction(row.action),  # type: ignore[attr-defined]
            actor=Actor(
                identifier=row.actor_identifier,  # type: ignore[attr-defined]
                kind=row.actor_kind,  # type: ignore[attr-defined]
            ),
            person_uuid=row.person_uuid,  # type: ignore[attr-defined]
            face_sample_uuid=row.face_sample_uuid,  # type: ignore[attr-defined]
            identification_uuid=row.identification_uuid,  # type: ignore[attr-defined]
            policy_version=row.policy_version,  # type: ignore[attr-defined]
            details=row.details,  # type: ignore[attr-defined]
        )


class SqlAlchemyIdentificationStore:
    """Persistence of identification attempts and their reviews."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the store to an open session."""
        self._session = session

    async def add(
        self, identification_uuid: UUID, query_sha256: str, decision: IdentityDecision
    ) -> None:
        """Record an identification and the policy that produced it."""
        best = decision.best
        await self._session.execute(
            identifications.insert().values(
                identification_uuid=identification_uuid,
                query_sha256=query_sha256,
                outcome=decision.outcome.value,
                policy_version=decision.thresholds.policy_version,
                accept_at=decision.thresholds.accept_at,
                review_at=decision.thresholds.review_at,
                best_person_uuid=best.person_uuid if best else None,
                best_score=best.score if best else None,
                candidates=json.loads(
                    json.dumps([asdict(c) for c in decision.candidates], default=str)
                ),
            )
        )

    async def get(self, identification_uuid: UUID) -> StoredIdentification | None:
        """Return one identification, or None."""
        result = await self._session.execute(
            select(identifications).where(
                identifications.c.identification_uuid == identification_uuid
            )
        )
        row = result.one_or_none()
        if row is None:
            return None
        return StoredIdentification(
            identification_uuid=row.identification_uuid,
            query_sha256=row.query_sha256,
            decision=IdentityDecision(
                outcome=DecisionOutcome(row.outcome),
                thresholds=DecisionThresholds(
                    accept_at=row.accept_at,
                    review_at=row.review_at,
                    policy_version=row.policy_version,
                ),
                candidates=tuple(
                    Candidate(
                        person_uuid=UUID(c["person_uuid"]),
                        face_sample_uuid=UUID(c["face_sample_uuid"]),
                        score=c["score"],
                        sample_count=c["sample_count"],
                    )
                    for c in row.candidates
                ),
            ),
            created_at=row.created_at,
            review_outcome=ReviewOutcome(row.review_outcome) if row.review_outcome else None,
            reviewed_by=row.reviewed_by,
            reviewed_at=row.reviewed_at,
            review_note=row.review_note,
        )

    async def record_review(
        self,
        identification_uuid: UUID,
        *,
        outcome: ReviewOutcome,
        reviewer: str,
        note: str | None,
        reviewed_at: datetime,
    ) -> None:
        """Attach a human's conclusion to an identification.

        Refuses to overwrite an existing review: changing a recorded judgement
        would erase the first one, and the log exists precisely so that cannot
        happen silently.
        """
        result = await self._session.execute(
            identifications.update()
            .where(
                identifications.c.identification_uuid == identification_uuid,
                identifications.c.review_outcome.is_(None),
            )
            .values(
                review_outcome=outcome.value,
                reviewed_by=reviewer,
                reviewed_at=reviewed_at,
                review_note=note,
            )
        )
        if cast("CursorResult[Any]", result).rowcount == 0:
            raise ConflictError(
                f"identification {identification_uuid} is unknown or already reviewed"
            )
