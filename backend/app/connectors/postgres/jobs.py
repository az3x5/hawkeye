"""PostgreSQL-backed durable processing queue and operator queries."""

from __future__ import annotations

import builtins
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import Row, and_, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.postgres.processing_tables import (
    processing_job_attempts,
    processing_jobs,
    processing_outbox_events,
    processing_worker_heartbeats,
)
from app.domain.processing import (
    JobCounts,
    JobErrorCode,
    JobLeaseError,
    JobPriority,
    JobReservation,
    JobStateError,
    JobStatus,
    ProcessingEventType,
    ProcessingJob,
    ProcessingMetrics,
)

MAX_ERROR_DETAIL = 2000
TERMINAL_STATUSES = frozenset(
    {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.DEAD_LETTER, JobStatus.CANCELLED}
)


def _to_job(row: Row[tuple[object, ...]]) -> ProcessingJob:
    return ProcessingJob(
        job_uuid=row.job_uuid,
        pipeline=row.pipeline,
        pipeline_version=row.pipeline_version,
        subject_type=row.subject_type,
        subject_uuid=row.subject_uuid,
        idempotency_key=row.idempotency_key,
        payload=dict(row.payload),
        status=JobStatus(row.status),
        priority=JobPriority(row.priority),
        attempt_count=row.attempt_count,
        max_attempts=row.max_attempts,
        queued_at=row.queued_at,
        available_at=row.available_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
        leased_until=row.leased_until,
        lease_uuid=row.lease_uuid,
        worker_id=row.worker_id,
        error_code=JobErrorCode(row.error_code) if row.error_code else None,
        error_detail=row.error_detail,
    )


class SqlAlchemyProcessingJobRepository:
    """Durable job state over one PostgreSQL transaction."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        retry_base_seconds: int = 5,
        retry_max_seconds: int = 300,
    ) -> None:
        """Bind the repository and bounded exponential retry policy."""
        if retry_base_seconds < 1 or retry_max_seconds < retry_base_seconds:
            raise ValueError("retry delays must be positive and max must be at least base")
        self._session = session
        self._retry_base_seconds = retry_base_seconds
        self._retry_max_seconds = retry_max_seconds

    async def enqueue(self, job: ProcessingJob) -> tuple[ProcessingJob, bool]:
        """Insert work once per pipeline/idempotency key."""
        result = await self._session.execute(
            insert(processing_jobs)
            .values(
                job_uuid=job.job_uuid,
                pipeline=job.pipeline,
                pipeline_version=job.pipeline_version,
                subject_type=job.subject_type,
                subject_uuid=job.subject_uuid,
                idempotency_key=job.idempotency_key,
                payload=job.payload,
                status=job.status.value,
                priority=int(job.priority),
                attempt_count=job.attempt_count,
                max_attempts=job.max_attempts,
                queued_at=job.queued_at,
                available_at=job.available_at,
            )
            .on_conflict_do_nothing(constraint="uq_processing_job_idempotency")
        )
        if cast("CursorResult[Any]", result).rowcount > 0:
            await self._emit(
                job_uuid=job.job_uuid,
                pipeline=job.pipeline,
                event_type=ProcessingEventType.QUEUED,
                status=job.status,
                attempt_count=job.attempt_count,
                details={
                    "subject_type": job.subject_type,
                    "subject_uuid": str(job.subject_uuid),
                },
            )
            return job, True
        existing = await self.find_by_idempotency(job.pipeline, job.idempotency_key)
        if existing is None:
            raise RuntimeError("processing job conflict could not be resolved")
        return existing, False

    async def get(self, job_uuid: UUID) -> ProcessingJob | None:
        """Return one job."""
        result = await self._session.execute(
            select(processing_jobs).where(processing_jobs.c.job_uuid == job_uuid)
        )
        row = result.one_or_none()
        return _to_job(row) if row is not None else None

    async def find_by_idempotency(
        self, pipeline: str, idempotency_key: str
    ) -> ProcessingJob | None:
        """Return the logical job represented by an idempotency key."""
        result = await self._session.execute(
            select(processing_jobs).where(
                processing_jobs.c.pipeline == pipeline,
                processing_jobs.c.idempotency_key == idempotency_key,
            )
        )
        row = result.one_or_none()
        return _to_job(row) if row is not None else None

    async def claim(
        self,
        pipeline: str,
        *,
        worker_id: str,
        lease_seconds: int,
    ) -> JobReservation | None:
        """Claim one available job using a race-safe PostgreSQL lease."""
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        now = datetime.now(UTC)
        await self._expire_leases(now)

        result = await self._session.execute(
            select(processing_jobs)
            .where(
                processing_jobs.c.pipeline == pipeline,
                processing_jobs.c.status.in_([JobStatus.QUEUED.value, JobStatus.RETRY.value]),
                processing_jobs.c.available_at <= now,
                processing_jobs.c.attempt_count < processing_jobs.c.max_attempts,
            )
            .order_by(
                processing_jobs.c.priority,
                processing_jobs.c.available_at,
                processing_jobs.c.queued_at,
                processing_jobs.c.job_uuid,
            )
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        row = result.one_or_none()
        if row is None:
            await self.heartbeat(worker_id, pipeline, current_job_uuid=None)
            return None

        lease_uuid = uuid4()
        leased_until = now + timedelta(seconds=lease_seconds)
        attempt_number = row.attempt_count + 1
        await self._session.execute(
            processing_jobs.update()
            .where(processing_jobs.c.job_uuid == row.job_uuid)
            .values(
                status=JobStatus.RUNNING.value,
                attempt_count=attempt_number,
                started_at=func.coalesce(processing_jobs.c.started_at, now),
                leased_until=leased_until,
                lease_uuid=lease_uuid,
                worker_id=worker_id,
                completed_at=None,
                error_code=None,
                error_detail=None,
            )
        )
        await self._session.execute(
            processing_job_attempts.insert().values(
                attempt_uuid=uuid4(),
                job_uuid=row.job_uuid,
                attempt_number=attempt_number,
                lease_uuid=lease_uuid,
                worker_id=worker_id,
                started_at=now,
            )
        )
        await self.heartbeat(worker_id, pipeline, current_job_uuid=row.job_uuid)
        await self._emit(
            job_uuid=row.job_uuid,
            pipeline=pipeline,
            event_type=ProcessingEventType.CLAIMED,
            status=JobStatus.RUNNING,
            attempt_count=attempt_number,
            details={
                "worker_id": worker_id,
                "lease_uuid": str(lease_uuid),
            },
        )
        claimed = await self.get(row.job_uuid)
        if claimed is None:
            raise RuntimeError("claimed processing job disappeared")
        return JobReservation(
            job=claimed,
            lease_uuid=lease_uuid,
            worker_id=worker_id,
            leased_until=leased_until,
            attempt_number=attempt_number,
        )

    async def complete(self, reservation: JobReservation) -> None:
        """Complete only the currently owned lease."""
        now = datetime.now(UTC)
        result = await self._session.execute(
            processing_jobs.update()
            .where(*self._owned_lease(reservation, now=now))
            .values(
                status=JobStatus.COMPLETED.value,
                completed_at=now,
                leased_until=None,
                lease_uuid=None,
                error_code=None,
                error_detail=None,
            )
        )
        if cast("CursorResult[Any]", result).rowcount == 0:
            raise JobLeaseError(f"job {reservation.job.job_uuid} lease is no longer owned")
        await self._close_attempt(reservation.lease_uuid, JobStatus.COMPLETED, now=now)
        await self._emit(
            job_uuid=reservation.job.job_uuid,
            pipeline=reservation.job.pipeline,
            event_type=ProcessingEventType.COMPLETED,
            status=JobStatus.COMPLETED,
            attempt_count=reservation.attempt_number,
            details={"worker_id": reservation.worker_id},
        )
        await self.heartbeat(reservation.worker_id, reservation.job.pipeline, current_job_uuid=None)

    async def fail(
        self,
        reservation: JobReservation,
        *,
        error_code: JobErrorCode,
        detail: str,
        retryable: bool,
    ) -> JobStatus:
        """Record failure, scheduling bounded retry only when permitted."""
        now = datetime.now(UTC)
        detail = detail[:MAX_ERROR_DETAIL]
        if retryable and reservation.attempt_number < reservation.job.max_attempts:
            outcome = JobStatus.RETRY
            delay = min(
                self._retry_base_seconds * (2 ** (reservation.attempt_number - 1)),
                self._retry_max_seconds,
            )
            available_at = now + timedelta(seconds=delay)
            completed_at = None
        else:
            outcome = JobStatus.DEAD_LETTER if retryable else JobStatus.FAILED
            available_at = now
            completed_at = now

        result = await self._session.execute(
            processing_jobs.update()
            .where(*self._owned_lease(reservation, now=now))
            .values(
                status=outcome.value,
                available_at=available_at,
                completed_at=completed_at,
                leased_until=None,
                lease_uuid=None,
                error_code=error_code.value,
                error_detail=detail,
            )
        )
        if cast("CursorResult[Any]", result).rowcount == 0:
            raise JobLeaseError(f"job {reservation.job.job_uuid} lease is no longer owned")
        await self._close_attempt(
            reservation.lease_uuid,
            outcome,
            now=now,
            error_code=error_code,
            detail=detail,
        )
        await self._emit(
            job_uuid=reservation.job.job_uuid,
            pipeline=reservation.job.pipeline,
            event_type=ProcessingEventType.FAILED,
            status=outcome,
            attempt_count=reservation.attempt_number,
            details={
                "worker_id": reservation.worker_id,
                "error_code": error_code.value,
                "retryable": retryable,
            },
        )
        await self.heartbeat(reservation.worker_id, reservation.job.pipeline, current_job_uuid=None)
        return outcome

    async def retry(self, job_uuid: UUID) -> ProcessingJob:
        """Give one terminal job one additional operator-authorized attempt."""
        now = datetime.now(UTC)
        result = await self._session.execute(
            processing_jobs.update()
            .where(
                processing_jobs.c.job_uuid == job_uuid,
                processing_jobs.c.status.in_(
                    [
                        JobStatus.FAILED.value,
                        JobStatus.DEAD_LETTER.value,
                        JobStatus.CANCELLED.value,
                    ]
                ),
            )
            .values(
                status=JobStatus.RETRY.value,
                max_attempts=processing_jobs.c.attempt_count + 1,
                available_at=now,
                completed_at=None,
                leased_until=None,
                lease_uuid=None,
                worker_id=None,
                error_code=None,
                error_detail=None,
            )
        )
        if cast("CursorResult[Any]", result).rowcount == 0:
            if await self.get(job_uuid) is None:
                raise KeyError(f"no processing job {job_uuid}")
            raise JobStateError(f"job {job_uuid} is not in a retryable state")
        retried = await self.get(job_uuid)
        if retried is None:
            raise RuntimeError("retried processing job disappeared")
        await self._emit(
            job_uuid=retried.job_uuid,
            pipeline=retried.pipeline,
            event_type=ProcessingEventType.RETRIED,
            status=retried.status,
            attempt_count=retried.attempt_count,
            details={"authorized_attempts": retried.max_attempts},
        )
        return retried

    async def cancel(self, job_uuid: UUID) -> ProcessingJob:
        """Cancel non-terminal work and invalidate any active lease."""
        now = datetime.now(UTC)
        result = await self._session.execute(
            select(processing_jobs).where(processing_jobs.c.job_uuid == job_uuid).with_for_update()
        )
        row = result.one_or_none()
        current = _to_job(row) if row is not None else None
        if current is None:
            raise KeyError(f"no processing job {job_uuid}")
        if current.status in TERMINAL_STATUSES:
            raise JobStateError(f"job {job_uuid} is already terminal ({current.status.value})")
        await self._session.execute(
            processing_jobs.update()
            .where(processing_jobs.c.job_uuid == job_uuid)
            .values(
                status=JobStatus.CANCELLED.value,
                completed_at=now,
                leased_until=None,
                lease_uuid=None,
                error_code=None,
                error_detail=None,
            )
        )
        if current.lease_uuid is not None:
            await self._close_attempt(current.lease_uuid, JobStatus.CANCELLED, now=now)
        cancelled = await self.get(job_uuid)
        if cancelled is None:
            raise RuntimeError("cancelled processing job disappeared")
        await self._emit(
            job_uuid=cancelled.job_uuid,
            pipeline=cancelled.pipeline,
            event_type=ProcessingEventType.CANCELLED,
            status=cancelled.status,
            attempt_count=cancelled.attempt_count,
            details={"worker_id": current.worker_id},
        )
        return cancelled

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        pipeline: str | None = None,
        status: JobStatus | None = None,
    ) -> tuple[list[ProcessingJob], int]:
        """Return newest jobs and a total for operator pagination."""
        conditions = []
        if pipeline is not None:
            conditions.append(processing_jobs.c.pipeline == pipeline)
        if status is not None:
            conditions.append(processing_jobs.c.status == status.value)
        where = and_(*conditions) if conditions else None
        counting = select(func.count()).select_from(processing_jobs)
        listing = select(processing_jobs)
        if where is not None:
            counting = counting.where(where)
            listing = listing.where(where)
        total = await self._session.scalar(counting)
        rows = await self._session.execute(
            listing.order_by(processing_jobs.c.queued_at.desc(), processing_jobs.c.job_uuid)
            .limit(max(1, min(limit, 200)))
            .offset(max(0, offset))
        )
        return [_to_job(row) for row in rows.all()], int(total or 0)

    async def counts(self) -> JobCounts:
        """Return state counts and oldest runnable work age anchor."""
        rows = await self._session.execute(
            select(processing_jobs.c.status, func.count().label("job_count")).group_by(
                processing_jobs.c.status
            )
        )
        oldest = await self._session.scalar(
            select(func.min(processing_jobs.c.queued_at)).where(
                processing_jobs.c.status.in_([JobStatus.QUEUED.value, JobStatus.RETRY.value])
            )
        )
        return JobCounts(
            by_status={
                JobStatus(row.status): cast("int", row._mapping["job_count"]) for row in rows.all()
            },
            oldest_queued_at=oldest,
        )

    async def metrics(
        self,
        *,
        now: datetime | None = None,
        live_worker_window_seconds: int = 600,
    ) -> ProcessingMetrics:
        """Return queue pressure, lease health, and one-minute throughput."""
        if live_worker_window_seconds < 1:
            raise ValueError("live worker window must be positive")
        measured_at = now or datetime.now(UTC)
        minute_ago = measured_at - timedelta(minutes=1)
        live_since = measured_at - timedelta(seconds=live_worker_window_seconds)
        counts = await self.counts()

        async def count_where(table: Any, *conditions: Any) -> int:
            value = await self._session.scalar(
                select(func.count()).select_from(table).where(*conditions)
            )
            return int(value or 0)

        active_leases = await count_where(
            processing_jobs,
            processing_jobs.c.status == JobStatus.RUNNING.value,
            processing_jobs.c.leased_until >= measured_at,
        )
        expired_leases = await count_where(
            processing_jobs,
            processing_jobs.c.status == JobStatus.RUNNING.value,
            processing_jobs.c.leased_until < measured_at,
        )
        completed_last_minute = await count_where(
            processing_job_attempts,
            processing_job_attempts.c.outcome == JobStatus.COMPLETED.value,
            processing_job_attempts.c.completed_at >= minute_ago,
        )
        attempts_last_minute = await count_where(
            processing_job_attempts,
            processing_job_attempts.c.started_at >= minute_ago,
        )
        live_workers = await count_where(
            processing_worker_heartbeats,
            processing_worker_heartbeats.c.last_seen_at >= live_since,
        )
        oldest_age = (
            max((measured_at - counts.oldest_queued_at).total_seconds(), 0.0)
            if counts.oldest_queued_at is not None
            else None
        )
        return ProcessingMetrics(
            by_status=counts.by_status,
            queue_depth=sum(
                counts.by_status.get(state, 0) for state in (JobStatus.QUEUED, JobStatus.RETRY)
            ),
            oldest_queued_age_seconds=oldest_age,
            active_leases=active_leases,
            expired_leases=expired_leases,
            completed_last_minute=completed_last_minute,
            attempts_last_minute=attempts_last_minute,
            live_workers=live_workers,
        )

    async def heartbeat(
        self,
        worker_id: str,
        pipeline: str,
        *,
        current_job_uuid: UUID | None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Upsert worker presence without granting or extending a lease."""
        await self._session.execute(
            insert(processing_worker_heartbeats)
            .values(
                worker_id=worker_id,
                pipeline=pipeline,
                last_seen_at=datetime.now(UTC),
                current_job_uuid=current_job_uuid,
                details=details or {},
            )
            .on_conflict_do_update(
                index_elements=[
                    processing_worker_heartbeats.c.worker_id,
                    processing_worker_heartbeats.c.pipeline,
                ],
                set_={
                    "last_seen_at": datetime.now(UTC),
                    "current_job_uuid": current_job_uuid,
                    "details": details or {},
                },
            )
        )

    async def attempts_for_job(self, job_uuid: UUID) -> builtins.list[dict[str, Any]]:
        """Return attempt history for tests and operator detail."""
        rows = await self._session.execute(
            select(processing_job_attempts)
            .where(processing_job_attempts.c.job_uuid == job_uuid)
            .order_by(processing_job_attempts.c.attempt_number)
        )
        return [dict(row._mapping) for row in rows.all()]

    async def _expire_leases(self, now: datetime) -> None:
        result = await self._session.execute(
            select(processing_jobs)
            .where(
                processing_jobs.c.status == JobStatus.RUNNING.value,
                processing_jobs.c.leased_until < now,
            )
            .with_for_update(skip_locked=True)
        )
        for row in result.all():
            outcome = (
                JobStatus.DEAD_LETTER if row.attempt_count >= row.max_attempts else JobStatus.RETRY
            )
            detail = "worker lease expired before completion"
            await self._session.execute(
                processing_jobs.update()
                .where(processing_jobs.c.job_uuid == row.job_uuid)
                .values(
                    status=outcome.value,
                    available_at=now,
                    completed_at=now if outcome is JobStatus.DEAD_LETTER else None,
                    leased_until=None,
                    lease_uuid=None,
                    error_code=JobErrorCode.INTERNAL_ERROR.value,
                    error_detail=detail,
                )
            )
            if row.lease_uuid is not None:
                await self._close_attempt(
                    row.lease_uuid,
                    JobStatus.DEAD_LETTER if outcome is JobStatus.DEAD_LETTER else JobStatus.RETRY,
                    now=now,
                    error_code=JobErrorCode.INTERNAL_ERROR,
                    detail=detail,
                    lease_expired=True,
                )
            await self._emit(
                job_uuid=row.job_uuid,
                pipeline=row.pipeline,
                event_type=ProcessingEventType.LEASE_EXPIRED,
                status=outcome,
                attempt_count=row.attempt_count,
                details={
                    "worker_id": row.worker_id,
                    "error_code": JobErrorCode.INTERNAL_ERROR.value,
                },
            )

    async def _close_attempt(
        self,
        lease_uuid: UUID,
        outcome: JobStatus,
        *,
        now: datetime,
        error_code: JobErrorCode | None = None,
        detail: str | None = None,
        lease_expired: bool = False,
    ) -> None:
        await self._session.execute(
            processing_job_attempts.update()
            .where(
                processing_job_attempts.c.lease_uuid == lease_uuid,
                processing_job_attempts.c.completed_at.is_(None),
            )
            .values(
                completed_at=now,
                outcome="lease_expired" if lease_expired else outcome.value,
                error_code=error_code.value if error_code else None,
                error_detail=detail[:MAX_ERROR_DETAIL] if detail else None,
            )
        )

    @staticmethod
    def _owned_lease(reservation: JobReservation, *, now: datetime) -> tuple[Any, ...]:
        return (
            processing_jobs.c.job_uuid == reservation.job.job_uuid,
            processing_jobs.c.status == JobStatus.RUNNING.value,
            processing_jobs.c.leased_until >= now,
            processing_jobs.c.lease_uuid == reservation.lease_uuid,
            processing_jobs.c.worker_id == reservation.worker_id,
        )

    async def _emit(
        self,
        *,
        job_uuid: UUID,
        pipeline: str,
        event_type: ProcessingEventType,
        status: JobStatus,
        attempt_count: int,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Write a notification event in the caller's job transaction."""
        payload: dict[str, Any] = {
            "job_uuid": str(job_uuid),
            "pipeline": pipeline,
            "status": status.value,
            "attempt_count": attempt_count,
        }
        payload.update(details or {})
        if any(
            key in payload for key in {"image", "media", "embedding", "text", "token", "secret"}
        ):
            raise ValueError("outbox payload contains a prohibited sensitive field")
        await self._session.execute(
            processing_outbox_events.insert().values(
                event_uuid=uuid4(),
                aggregate_type="processing_job",
                aggregate_uuid=job_uuid,
                event_type=event_type.value,
                payload=payload,
            )
        )


__all__ = ["SqlAlchemyProcessingJobRepository"]
