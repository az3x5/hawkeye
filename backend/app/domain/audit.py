"""Audit records.

Every administrative and review action leaves a record. The log is
append-only by design: there is no update and no delete, because a record that
can be altered afterwards is not evidence.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable
from uuid import UUID, uuid4


class AuditAction(StrEnum):
    """The actions worth recording."""

    IDENTIFICATION_PERFORMED = "identification_performed"
    IDENTIFICATION_REVIEWED = "identification_reviewed"


@dataclass(frozen=True, slots=True)
class Actor:
    """Who took an action.

    ``kind`` distinguishes a person from the system itself, so an automatic
    decision can never be mistaken for a human's judgement when the log is read
    back.
    """

    identifier: str
    kind: str = "user"

    def __post_init__(self) -> None:
        """Validate the actor's identity."""
        if not self.identifier.strip():
            raise ValueError("an actor must be identified")
        if self.kind not in {"user", "system"}:
            raise ValueError(f"actor kind must be 'user' or 'system', got {self.kind!r}")


#: The service acting on its own behalf, e.g. an automatic decision. Kept
#: distinct from any human so the log can never blur the two.
SYSTEM_ACTOR = Actor(identifier="faceid", kind="system")


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """One recorded action."""

    action: AuditAction
    actor: Actor
    audit_uuid: UUID = field(default_factory=uuid4)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    person_uuid: UUID | None = None
    face_sample_uuid: UUID | None = None
    identification_uuid: UUID | None = None
    policy_version: str | None = None
    #: Structured context. Identifiers, scores and thresholds only — never
    #: image bytes and never an embedding.
    details: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class AuditLog(Protocol):
    """Append-only storage of audit events."""

    async def record(self, event: AuditEvent) -> AuditEvent:
        """Append an event. There is deliberately no update or delete."""
        ...

    async def for_person(self, person_uuid: UUID, *, limit: int = 100) -> Sequence[AuditEvent]:
        """Return the events touching a person, most recent first."""
        ...

    async def for_identification(self, identification_uuid: UUID) -> Sequence[AuditEvent]:
        """Return the events belonging to one identification, oldest first."""
        ...
