"""Durable asynchronous processing contracts.

PostgreSQL is the authority for job state. Queue transports may wake workers,
but they never become the only record that work exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import IntEnum, StrEnum
from typing import Any, Protocol, runtime_checkable
from uuid import UUID, uuid4


class ProcessingEventType(StrEnum):
    """Stable event names emitted transactionally with job transitions."""

    QUEUED = "processing.job.queued"
    CLAIMED = "processing.job.claimed"
    COMPLETED = "processing.job.completed"
    FAILED = "processing.job.failed"
    RETRIED = "processing.job.retried"
    CANCELLED = "processing.job.cancelled"
    LEASE_EXPIRED = "processing.job.lease_expired"


class JobStatus(StrEnum):
    """Durable processing lifecycle."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRY = "retry"
    DEAD_LETTER = "dead_letter"
    CANCELLED = "cancelled"


class JobPriority(IntEnum):
    """Smaller values are claimed first."""

    CRITICAL = 0
    HIGH = 10
    NORMAL = 20
    LOW = 30
    BULK = 40


class JobErrorCode(StrEnum):
    """Stable machine-readable failure categories."""

    STORAGE_UNAVAILABLE = "storage_unavailable"
    QDRANT_UNAVAILABLE = "qdrant_unavailable"
    DATABASE_UNAVAILABLE = "database_unavailable"
    MODEL_LOAD_FAILED = "model_load_failed"
    INVALID_IMAGE = "invalid_image"
    NO_FACE = "no_face"
    MULTIPLE_FACES = "multiple_faces"
    SOURCE_UNAVAILABLE = "source_unavailable"
    RATE_LIMITED = "rate_limited"
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN = "forbidden"
    INVALID_MEDIA = "invalid_media"
    OCR_FAILED = "ocr_failed"
    VIDEO_DECODE_FAILED = "video_decode_failed"
    AUDIO_DECODE_FAILED = "audio_decode_failed"
    INTERNAL_ERROR = "internal_error"


class JobStateError(Exception):
    """A requested job transition is invalid."""


class JobLeaseError(JobStateError):
    """A worker tried to finish work after losing its lease."""


@dataclass(frozen=True, slots=True)
class ProcessingJob:
    """One idempotent unit of durable background work."""

    pipeline: str
    pipeline_version: str
    subject_type: str
    subject_uuid: UUID
    idempotency_key: str
    payload: dict[str, Any]
    job_uuid: UUID = field(default_factory=uuid4)
    status: JobStatus = JobStatus.QUEUED
    priority: JobPriority = JobPriority.NORMAL
    attempt_count: int = 0
    max_attempts: int = 3
    queued_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    available_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    leased_until: datetime | None = None
    lease_uuid: UUID | None = None
    worker_id: str | None = None
    error_code: JobErrorCode | None = None
    error_detail: str | None = None

    def __post_init__(self) -> None:
        """Reject unsafe or unusable job envelopes."""
        for label, value in (
            ("pipeline", self.pipeline),
            ("pipeline_version", self.pipeline_version),
            ("subject_type", self.subject_type),
            ("idempotency_key", self.idempotency_key),
        ):
            if not value.strip():
                raise ValueError(f"{label} must not be empty")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        if self.attempt_count < 0:
            raise ValueError("attempt_count must not be negative")
        if _contains_binary(self.payload):
            raise ValueError("job payloads must not contain binary data")


def _contains_binary(value: object) -> bool:
    if isinstance(value, bytes | bytearray | memoryview):
        return True
    if isinstance(value, dict):
        return any(_contains_binary(key) or _contains_binary(item) for key, item in value.items())
    if isinstance(value, list | tuple):
        return any(_contains_binary(item) for item in value)
    return False


@dataclass(frozen=True, slots=True)
class JobReservation:
    """A job claimed by one worker under an expiring lease token."""

    job: ProcessingJob
    lease_uuid: UUID
    worker_id: str
    leased_until: datetime
    attempt_number: int


@dataclass(frozen=True, slots=True)
class JobCounts:
    """Operational job counts grouped by state."""

    by_status: dict[JobStatus, int]
    oldest_queued_at: datetime | None


@dataclass(frozen=True, slots=True)
class ProcessingMetrics:
    """Live queue pressure, lease health, and recent throughput."""

    by_status: dict[JobStatus, int]
    queue_depth: int
    oldest_queued_age_seconds: float | None
    active_leases: int
    expired_leases: int
    completed_last_minute: int
    attempts_last_minute: int
    live_workers: int


@runtime_checkable
class ProcessingJobStore(Protocol):
    """Persistence contract for durable work."""

    async def enqueue(self, job: ProcessingJob) -> tuple[ProcessingJob, bool]:
        """Persist a job idempotently."""
        ...

    async def claim(
        self, pipeline: str, *, worker_id: str, lease_seconds: int
    ) -> JobReservation | None:
        """Claim the highest-priority available job."""
        ...

    async def complete(self, reservation: JobReservation) -> None:
        """Complete a reservation if its lease is still owned."""
        ...

    async def fail(
        self,
        reservation: JobReservation,
        *,
        error_code: JobErrorCode,
        detail: str,
        retryable: bool,
    ) -> JobStatus:
        """Record failure and return the resulting state."""
        ...
