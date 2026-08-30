"""Durable processing submission and operator actions."""

from __future__ import annotations

import builtins
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.connectors.postgres.jobs import SqlAlchemyProcessingJobRepository
from app.domain.audit import Actor, AuditAction, AuditEvent, AuditLog
from app.domain.jobs import EmbeddingJob, LanguageEmbeddingJob
from app.domain.processing import JobCounts, JobPriority, JobStatus, ProcessingJob

FACE_EMBEDDING_PIPELINE = "face_embedding"
FACE_EMBEDDING_PIPELINE_VERSION = "1"
LANGUAGE_EMBEDDING_PIPELINE = "language_embedding"
LANGUAGE_EMBEDDING_PIPELINE_VERSION = "1"


class FaceJobSubmitter:
    """Persist face work in the same transaction as its face sample."""

    def __init__(self, repository: SqlAlchemyProcessingJobRepository, *, max_attempts: int) -> None:
        """Bind the durable repository and automatic attempt budget."""
        self._repository = repository
        self._max_attempts = max_attempts

    async def enqueue(self, job: EmbeddingJob) -> None:
        """Store one logical face job idempotently."""
        await self._repository.enqueue(
            ProcessingJob(
                pipeline=FACE_EMBEDDING_PIPELINE,
                pipeline_version=FACE_EMBEDDING_PIPELINE_VERSION,
                subject_type="face_sample",
                subject_uuid=job.face_sample_uuid,
                idempotency_key=(f"{job.face_sample_uuid}:{FACE_EMBEDDING_PIPELINE_VERSION}"),
                payload=job.to_payload(),
                priority=JobPriority.NORMAL,
                max_attempts=self._max_attempts,
                queued_at=job.enqueued_at,
                available_at=job.enqueued_at,
            )
        )


class LanguageJobSubmitter:
    """Persist language work in the document transaction."""

    def __init__(self, repository: SqlAlchemyProcessingJobRepository, *, max_attempts: int) -> None:
        """Bind the durable repository and automatic attempt budget."""
        self._repository = repository
        self._max_attempts = max_attempts

    async def enqueue(self, job: LanguageEmbeddingJob) -> None:
        """Store one logical language job idempotently."""
        await self._repository.enqueue(
            ProcessingJob(
                pipeline=LANGUAGE_EMBEDDING_PIPELINE,
                pipeline_version=LANGUAGE_EMBEDDING_PIPELINE_VERSION,
                subject_type="language_document",
                subject_uuid=job.document_uuid,
                idempotency_key=(f"{job.document_uuid}:{LANGUAGE_EMBEDDING_PIPELINE_VERSION}"),
                payload=job.to_payload(),
                priority=JobPriority.NORMAL,
                max_attempts=self._max_attempts,
                queued_at=job.enqueued_at,
                available_at=job.enqueued_at,
            )
        )


@dataclass(frozen=True, slots=True)
class ProcessingJobPage:
    """Paginated operator view."""

    items: list[ProcessingJob]
    total: int
    limit: int
    offset: int


class ProcessingJobAdministration:
    """Read and control durable work with an append-only operator audit."""

    def __init__(self, repository: SqlAlchemyProcessingJobRepository, audit: AuditLog) -> None:
        """Bind job state and its append-only audit log."""
        self._repository = repository
        self._audit = audit

    async def get(self, job_uuid: UUID) -> ProcessingJob | None:
        """Return one durable job."""
        return await self._repository.get(job_uuid)

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        pipeline: str | None,
        status: JobStatus | None,
    ) -> ProcessingJobPage:
        """Return one filtered operator page."""
        jobs, total = await self._repository.list(
            limit=limit, offset=offset, pipeline=pipeline, status=status
        )
        return ProcessingJobPage(items=jobs, total=total, limit=limit, offset=offset)

    async def attempts(self, job_uuid: UUID) -> builtins.list[dict[str, Any]]:
        """Return immutable attempt history for operator detail."""
        return await self._repository.attempts_for_job(job_uuid)

    async def counts(self) -> JobCounts:
        """Return repository state counts without duplicating its domain type."""
        return await self._repository.counts()

    async def retry(self, job_uuid: UUID, *, actor: Actor) -> ProcessingJob:
        """Authorize one additional attempt and audit the operator action."""
        job = await self._repository.retry(job_uuid)
        await self._audit.record(
            AuditEvent(
                action=AuditAction.PROCESSING_JOB_RETRIED,
                actor=actor,
                details={"job_uuid": str(job_uuid), "pipeline": job.pipeline},
            )
        )
        return job

    async def cancel(self, job_uuid: UUID, *, actor: Actor) -> ProcessingJob:
        """Cancel non-terminal work and audit the operator action."""
        job = await self._repository.cancel(job_uuid)
        await self._audit.record(
            AuditEvent(
                action=AuditAction.PROCESSING_JOB_CANCELLED,
                actor=actor,
                details={"job_uuid": str(job_uuid), "pipeline": job.pipeline},
            )
        )
        return job
