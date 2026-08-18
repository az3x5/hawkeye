"""Identity decisions.

This is where similarity scores become actions. It is kept rigorously separate
from recognition: recognition measures, this decides, and the two must be able
to change independently — a threshold change is a policy event, not a model
change.

Nothing here converts a similarity into a probability. Doing so would imply a
calibrated model and a known prior population, and we have neither.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import UUID

from app.domain.vectors import VectorMatch


class DecisionOutcome(StrEnum):
    """What the system proposes to do with an identification."""

    #: Confident enough to act on without a human.
    ACCEPT = "accept"
    #: Plausible, but a person must look at it.
    REVIEW = "review"
    #: Not similar enough to anyone known.
    REJECT = "reject"


class ReviewOutcome(StrEnum):
    """What a human concluded about an identification."""

    CONFIRMED = "confirmed"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class DecisionThresholds:
    """The similarity cut-offs in force, and the name of the policy setting them.

    Thresholds are configuration rather than constants because they are a
    policy choice: they vary by deployment, population and risk appetite, and a
    value baked into code cannot be reviewed, tuned or explained after a
    contested decision. ``policy_version`` is recorded with every decision so a
    past decision can be re-read against the rules that actually produced it.
    """

    accept_at: float
    review_at: float
    policy_version: str

    def __post_init__(self) -> None:
        """Validate the ordering and ranges the thresholds must satisfy."""
        for name, value in (("accept_at", self.accept_at), ("review_at", self.review_at)):
            if not -1.0 <= value <= 1.0:
                raise ValueError(f"{name} must be a cosine similarity in [-1, 1], got {value}")
        if self.accept_at <= self.review_at:
            raise ValueError(
                f"accept_at ({self.accept_at}) must be above review_at ({self.review_at}); "
                "otherwise there is no band in which a human is asked to look"
            )
        if not self.policy_version.strip():
            raise ValueError("policy_version must not be empty")


@dataclass(frozen=True, slots=True)
class Candidate:
    """A known person who might be the one in the query image.

    ``score`` is the similarity of the *best matching sample* this person has.
    ``sample_count`` says how many of their samples were seen at all, so a
    reviewer can tell a single lucky match from a consistent one.
    """

    person_uuid: UUID
    face_sample_uuid: UUID
    score: float
    sample_count: int

    def __post_init__(self) -> None:
        """Validate the score range and sample count."""
        if not -1.0 <= self.score <= 1.0:
            raise ValueError(f"score must be a cosine similarity in [-1, 1], got {self.score}")
        if self.sample_count < 1:
            raise ValueError(
                f"a candidate must rest on at least one sample, got {self.sample_count}"
            )


@dataclass(frozen=True, slots=True)
class IdentityDecision:
    """The proposal, the evidence for it, and the rules that produced it."""

    outcome: DecisionOutcome
    thresholds: DecisionThresholds
    candidates: tuple[Candidate, ...]

    @property
    def best(self) -> Candidate | None:
        """The strongest candidate, or None when nobody matched at all."""
        return self.candidates[0] if self.candidates else None

    @property
    def margin(self) -> float | None:
        """Gap between the best and second-best candidate.

        A decision resting on a hair's-breadth margin deserves different
        treatment from a clear one, so the number is surfaced rather than
        buried; acting on it is the reviewer's call.
        """
        if len(self.candidates) < 2:
            return None
        return self.candidates[0].score - self.candidates[1].score


def aggregate_candidates(matches: Sequence[VectorMatch]) -> tuple[Candidate, ...]:
    """Collapse per-sample matches into one candidate per person.

    A person has many samples, so a raw neighbour list can be several rows of
    the same person. Each person is scored by their best-matching sample: a
    person photographed from an unhelpful angle should not be penalised for
    also having a poor sample on file. The alternative — averaging — would
    punish exactly the diversity of samples the system is designed to collect.
    """
    best: dict[UUID, VectorMatch] = {}
    counts: dict[UUID, int] = {}
    for match in matches:
        counts[match.person_uuid] = counts.get(match.person_uuid, 0) + 1
        current = best.get(match.person_uuid)
        if current is None or match.score > current.score:
            best[match.person_uuid] = match

    candidates = [
        Candidate(
            person_uuid=match.person_uuid,
            face_sample_uuid=match.face_sample_uuid,
            score=match.score,
            sample_count=counts[person_uuid],
        )
        for person_uuid, match in best.items()
    ]
    candidates.sort(key=lambda candidate: (-candidate.score, str(candidate.person_uuid)))
    return tuple(candidates)


def decide(matches: Sequence[VectorMatch], thresholds: DecisionThresholds) -> IdentityDecision:
    """Turn neighbours into a proposal, under the given thresholds.

    Three bands: at or above ``accept_at`` the system proposes a match; at or
    above ``review_at`` it asks a human; below that it proposes nobody. The
    bands are closed at the bottom so a score exactly on a threshold falls on
    the more cautious side of the boundary being crossed.
    """
    candidates = aggregate_candidates(matches)
    if not candidates:
        return IdentityDecision(
            outcome=DecisionOutcome.REJECT, thresholds=thresholds, candidates=()
        )

    top = candidates[0].score
    if top >= thresholds.accept_at:
        outcome = DecisionOutcome.ACCEPT
    elif top >= thresholds.review_at:
        outcome = DecisionOutcome.REVIEW
    else:
        outcome = DecisionOutcome.REJECT
    return IdentityDecision(outcome=outcome, thresholds=thresholds, candidates=candidates)


@dataclass(frozen=True, slots=True)
class StoredIdentification:
    """An identification as recorded, including any review."""

    identification_uuid: UUID
    query_sha256: str
    decision: IdentityDecision
    created_at: datetime
    review_outcome: ReviewOutcome | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    review_note: str | None = None


@runtime_checkable
class IdentificationStore(Protocol):
    """Persistence of identification attempts and their reviews."""

    async def add(
        self, identification_uuid: UUID, query_sha256: str, decision: IdentityDecision
    ) -> None:
        """Record an identification and the policy that produced it."""
        ...

    async def get(self, identification_uuid: UUID) -> StoredIdentification | None:
        """Return one identification, or None."""
        ...

    async def record_review(
        self,
        identification_uuid: UUID,
        *,
        outcome: ReviewOutcome,
        reviewer: str,
        note: str | None,
        reviewed_at: datetime,
    ) -> None:
        """Attach a human's conclusion. Refuses to overwrite an existing one."""
        ...

    async def awaiting_review(self, *, limit: int) -> Sequence[StoredIdentification]:
        """Return unreviewed proposals that asked for a human, oldest first.

        Oldest first because a review queue is a backlog: the item that has
        been waiting longest is the one most in need of attention.
        """
        ...
