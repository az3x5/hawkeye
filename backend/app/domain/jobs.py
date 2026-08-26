"""Background job contracts.

Detection and recognition are too slow and too memory-hungry to run inside a
request. The API records what should happen and hands off; a worker does the
work. This module describes that handoff without naming a queue technology.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import UUID


class ProcessingState(StrEnum):
    """Where a face sample has got to in the pipeline."""

    PENDING = "pending"
    PROCESSED = "processed"
    FAILED = "failed"


class JobQueueError(Exception):
    """The queue could not accept or deliver work."""


@dataclass(frozen=True, slots=True)
class EmbeddingJob:
    """A request to embed one stored face sample.

    Carries identifiers and a content hash — never image bytes and never a
    vector. The worker fetches the image from the object store itself.
    """

    face_sample_uuid: UUID
    person_uuid: UUID
    image_sha256: str
    enqueued_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_payload(self) -> dict[str, str]:
        """Render the job for transport."""
        return {
            "face_sample_uuid": str(self.face_sample_uuid),
            "person_uuid": str(self.person_uuid),
            "image_sha256": self.image_sha256,
            "enqueued_at": self.enqueued_at.isoformat(),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, str]) -> EmbeddingJob:
        """Rebuild a job from transport, rejecting anything malformed."""
        try:
            return cls(
                face_sample_uuid=UUID(payload["face_sample_uuid"]),
                person_uuid=UUID(payload["person_uuid"]),
                image_sha256=payload["image_sha256"],
                enqueued_at=datetime.fromisoformat(payload["enqueued_at"]),
            )
        except (KeyError, ValueError) as exc:
            raise JobQueueError(f"malformed embedding job payload: {exc}") from exc


@dataclass(frozen=True, slots=True)
class LanguageEmbeddingJob:
    """A request to embed one persisted language document."""

    document_uuid: UUID
    enqueued_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_payload(self) -> dict[str, str]:
        """Render the job for transport."""
        return {
            "document_uuid": str(self.document_uuid),
            "enqueued_at": self.enqueued_at.isoformat(),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, str]) -> LanguageEmbeddingJob:
        """Rebuild a language job from transport."""
        try:
            return cls(
                document_uuid=UUID(payload["document_uuid"]),
                enqueued_at=datetime.fromisoformat(payload["enqueued_at"]),
            )
        except (KeyError, ValueError) as exc:
            raise JobQueueError(f"malformed language embedding job payload: {exc}") from exc


@runtime_checkable
class JobQueue(Protocol):
    """A queue of embedding work."""

    async def enqueue(self, job: EmbeddingJob) -> None:
        """Submit a job for processing."""
        ...

    async def reserve(self, *, timeout_seconds: int) -> EmbeddingJob | None:
        """Take the next job, or return None if none arrives within the timeout.

        The job is held rather than dropped: it is only removed once the worker
        reports the outcome, so a worker that dies mid-job does not lose it.
        """
        ...

    async def complete(self, job: EmbeddingJob) -> None:
        """Report that a reserved job finished successfully."""
        ...

    async def fail(self, job: EmbeddingJob, reason: str) -> None:
        """Report that a reserved job failed, with a human-readable reason."""
        ...

    async def depth(self) -> int:
        """Number of jobs waiting to be reserved."""
        ...


@runtime_checkable
class ObjectStore(Protocol):
    """Blob storage for source images, addressed by content hash."""

    async def put(self, digest: str, data: bytes) -> None:
        """Store bytes under their content hash. Rewriting the same hash is a no-op."""
        ...

    async def get(self, digest: str) -> bytes | None:
        """Return the stored bytes, or None if absent."""
        ...

    async def delete(self, digest: str) -> bool:
        """Remove the object. Returns whether anything was removed."""
        ...

    async def list_digests(self, *, older_than: datetime | None = None) -> list[str]:
        """Return stored object addresses, optionally only those older than a time."""
        ...
