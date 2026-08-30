"""Integration contracts for the PostgreSQL durable processing queue."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text, update

from app.connectors.postgres import (
    PostgresConnector,
    SqlAlchemyProcessingJobRepository,
    metadata,
)
from app.connectors.postgres.audit import SqlAlchemyAuditLog
from app.connectors.postgres.processing_tables import processing_jobs
from app.domain.audit import Actor, AuditAction
from app.domain.processing import (
    JobErrorCode,
    JobLeaseError,
    JobPriority,
    JobReservation,
    JobStatus,
    ProcessingJob,
    ProcessingJobStore,
)
from app.services.processing import ProcessingJobAdministration

from .conftest import INTEGRATION_DSN

pytestmark = pytest.mark.skipif(
    INTEGRATION_DSN is None,
    reason="set FACEID_TEST_POSTGRES_DSN to run PostgreSQL integration tests",
)

PIPELINE = "test_pipeline"


def _job(
    *,
    priority: JobPriority = JobPriority.NORMAL,
    max_attempts: int = 3,
    idempotency_key: str | None = None,
) -> ProcessingJob:
    subject_uuid = uuid4()
    return ProcessingJob(
        pipeline=PIPELINE,
        pipeline_version="test-v1",
        subject_type="test_subject",
        subject_uuid=subject_uuid,
        idempotency_key=idempotency_key or str(subject_uuid),
        payload={"subject_uuid": str(subject_uuid)},
        priority=priority,
        max_attempts=max_attempts,
    )


@pytest_asyncio.fixture
async def connector() -> AsyncIterator[PostgresConnector]:
    assert INTEGRATION_DSN is not None
    instance = PostgresConnector(INTEGRATION_DSN)
    async with instance.engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
        await connection.execute(text("TRUNCATE processing.jobs CASCADE"))
        await connection.execute(text("TRUNCATE processing.outbox_events"))
        await connection.execute(text("TRUNCATE processing.worker_heartbeats"))
        await connection.execute(text("TRUNCATE audit_events"))
    yield instance
    async with instance.engine.begin() as connection:
        await connection.execute(text("TRUNCATE processing.jobs CASCADE"))
        await connection.execute(text("TRUNCATE processing.outbox_events"))
        await connection.execute(text("TRUNCATE processing.worker_heartbeats"))
        await connection.execute(text("TRUNCATE audit_events"))
    await instance.close()


async def _enqueue(connector: PostgresConnector, job: ProcessingJob) -> ProcessingJob:
    async with connector.session() as session:
        stored, _ = await SqlAlchemyProcessingJobRepository(session).enqueue(job)
        return stored


async def _claim(
    connector: PostgresConnector, worker_id: str = "worker-a"
) -> JobReservation | None:
    async with connector.session() as session:
        return await SqlAlchemyProcessingJobRepository(
            session, retry_base_seconds=1, retry_max_seconds=2
        ).claim(PIPELINE, worker_id=worker_id, lease_seconds=30)


async def _make_available(connector: PostgresConnector, job_uuid: object) -> None:
    async with connector.session() as session:
        await session.execute(
            update(processing_jobs)
            .where(processing_jobs.c.job_uuid == job_uuid)
            .values(available_at=datetime.now(UTC) - timedelta(seconds=1))
        )


async def test_repository_satisfies_the_processing_store_contract(
    connector: PostgresConnector,
) -> JobReservation | None:
    async with connector.session() as session:
        assert isinstance(SqlAlchemyProcessingJobRepository(session), ProcessingJobStore)


async def test_enqueue_is_idempotent_per_pipeline_and_key(
    connector: PostgresConnector,
) -> None:
    original = _job(idempotency_key="same-logical-work")
    async with connector.session() as session:
        repository = SqlAlchemyProcessingJobRepository(session)
        first, first_created = await repository.enqueue(original)
        second, second_created = await repository.enqueue(_job(idempotency_key="same-logical-work"))
        events = await session.execute(
            text(
                "SELECT event_type, payload FROM processing.outbox_events "
                "WHERE aggregate_uuid = :job_uuid"
            ),
            {"job_uuid": str(first.job_uuid)},
        )
    assert first_created is True
    assert second_created is False
    assert second.job_uuid == first.job_uuid
    event = events.one()
    assert event.event_type == "processing.job.queued"
    assert event.payload["subject_uuid"] == str(first.subject_uuid)
    assert not {"image", "embedding", "text", "token", "secret"} & set(event.payload)


async def test_claim_prefers_priority_and_only_one_worker_owns_each_job(
    connector: PostgresConnector,
) -> None:
    low = await _enqueue(connector, _job(priority=JobPriority.LOW))
    high = await _enqueue(connector, _job(priority=JobPriority.HIGH))
    first = await _claim(connector)
    second = await _claim(connector, "worker-b")
    assert first is not None and first.job.job_uuid == high.job_uuid
    assert second is not None and second.job.job_uuid == low.job_uuid
    assert first.lease_uuid != second.lease_uuid


async def test_concurrent_claims_cannot_duplicate_one_job(
    connector: PostgresConnector,
) -> None:
    await _enqueue(connector, _job())
    claims = await asyncio.gather(_claim(connector, "worker-a"), _claim(connector, "worker-b"))
    assert sum(claim is not None for claim in claims) == 1


async def test_success_closes_the_attempt_and_rejects_a_stale_lease(
    connector: PostgresConnector,
) -> None:
    stored = await _enqueue(connector, _job())
    reservation = await _claim(connector)
    assert reservation is not None
    async with connector.session() as session:
        repository = SqlAlchemyProcessingJobRepository(session)
        await repository.complete(reservation)
        completed = await repository.get(stored.job_uuid)
        attempts = await repository.attempts_for_job(stored.job_uuid)
        metrics = await repository.metrics()
        event_rows = await session.execute(
            text(
                "SELECT event_type FROM processing.outbox_events WHERE aggregate_uuid = :job_uuid"
            ),
            {"job_uuid": str(stored.job_uuid)},
        )
    assert completed is not None and completed.status is JobStatus.COMPLETED
    assert attempts[0]["outcome"] == JobStatus.COMPLETED.value
    assert metrics.completed_last_minute == 1
    assert metrics.attempts_last_minute == 1
    assert metrics.active_leases == 0
    assert {row.event_type for row in event_rows.all()} == {
        "processing.job.queued",
        "processing.job.claimed",
        "processing.job.completed",
    }
    with pytest.raises(JobLeaseError):
        async with connector.session() as session:
            await SqlAlchemyProcessingJobRepository(session).complete(reservation)


async def test_retryable_failures_back_off_then_dead_letter(
    connector: PostgresConnector,
) -> None:
    stored = await _enqueue(connector, _job(max_attempts=2))
    first = await _claim(connector)
    assert first is not None
    async with connector.session() as session:
        outcome = await SqlAlchemyProcessingJobRepository(
            session, retry_base_seconds=1, retry_max_seconds=2
        ).fail(
            first,
            error_code=JobErrorCode.QDRANT_UNAVAILABLE,
            detail="temporary outage",
            retryable=True,
        )
    assert outcome is JobStatus.RETRY
    assert await _claim(connector) is None
    await _make_available(connector, stored.job_uuid)
    second = await _claim(connector, "worker-b")
    assert second is not None and second.attempt_number == 2
    async with connector.session() as session:
        repository = SqlAlchemyProcessingJobRepository(session)
        outcome = await repository.fail(
            second,
            error_code=JobErrorCode.QDRANT_UNAVAILABLE,
            detail="still unavailable",
            retryable=True,
        )
        terminal = await repository.get(stored.job_uuid)
        attempts = await repository.attempts_for_job(stored.job_uuid)
    assert outcome is JobStatus.DEAD_LETTER
    assert terminal is not None and terminal.completed_at is not None
    assert [attempt["outcome"] for attempt in attempts] == ["retry", "dead_letter"]


async def test_permanent_failure_does_not_retry(connector: PostgresConnector) -> None:
    stored = await _enqueue(connector, _job())
    reservation = await _claim(connector)
    assert reservation is not None
    async with connector.session() as session:
        repository = SqlAlchemyProcessingJobRepository(session)
        outcome = await repository.fail(
            reservation,
            error_code=JobErrorCode.NO_FACE,
            detail="no face detected",
            retryable=False,
        )
        failed = await repository.get(stored.job_uuid)
    assert outcome is JobStatus.FAILED
    assert failed is not None and failed.error_code is JobErrorCode.NO_FACE
    assert await _claim(connector) is None


async def test_expired_lease_is_recovered_and_old_owner_is_fenced(
    connector: PostgresConnector,
) -> None:
    stored = await _enqueue(connector, _job(max_attempts=2))
    expired = await _claim(connector)
    assert expired is not None
    async with connector.session() as session:
        await session.execute(
            update(processing_jobs)
            .where(processing_jobs.c.job_uuid == stored.job_uuid)
            .values(leased_until=datetime.now(UTC) - timedelta(seconds=1))
        )
    with pytest.raises(JobLeaseError):
        async with connector.session() as session:
            await SqlAlchemyProcessingJobRepository(session).complete(expired)
    recovered = await _claim(connector, "worker-b")
    assert recovered is not None and recovered.attempt_number == 2
    async with connector.session() as session:
        attempts = await SqlAlchemyProcessingJobRepository(session).attempts_for_job(
            stored.job_uuid
        )
    assert attempts[0]["outcome"] == "lease_expired"


async def test_cancel_and_operator_retry_are_validated(connector: PostgresConnector) -> None:
    stored = await _enqueue(connector, _job())
    async with connector.session() as session:
        repository = SqlAlchemyProcessingJobRepository(session)
        cancelled = await repository.cancel(stored.job_uuid)
        retried = await repository.retry(stored.job_uuid)
    assert cancelled.status is JobStatus.CANCELLED
    assert retried.status is JobStatus.RETRY
    assert retried.max_attempts == 1


async def test_operator_retry_and_cancel_are_audited(connector: PostgresConnector) -> None:
    stored = await _enqueue(connector, _job())
    actor = Actor(identifier="operator@example.com")
    async with connector.session() as session:
        administration = ProcessingJobAdministration(
            SqlAlchemyProcessingJobRepository(session), SqlAlchemyAuditLog(session)
        )
        await administration.cancel(stored.job_uuid, actor=actor)
        await administration.retry(stored.job_uuid, actor=actor)
    async with connector.session() as session:
        rows = await session.execute(
            text("SELECT action, actor_identifier, details FROM audit_events ORDER BY occurred_at")
        )
    events = rows.all()
    assert [event.action for event in events] == [
        AuditAction.PROCESSING_JOB_CANCELLED.value,
        AuditAction.PROCESSING_JOB_RETRIED.value,
    ]
    assert all(event.actor_identifier == actor.identifier for event in events)
    assert all(event.details["job_uuid"] == str(stored.job_uuid) for event in events)


async def test_counts_include_oldest_runnable_job(connector: PostgresConnector) -> None:
    await _enqueue(connector, _job())
    await _enqueue(connector, _job())
    reservation = await _claim(connector)
    assert reservation is not None
    async with connector.session() as session:
        counts = await SqlAlchemyProcessingJobRepository(session).counts()
        metrics = await SqlAlchemyProcessingJobRepository(session).metrics()
    assert counts.by_status == {JobStatus.QUEUED: 1, JobStatus.RUNNING: 1}
    assert counts.oldest_queued_at is not None
    assert metrics.queue_depth == 1
    assert metrics.active_leases == 1
    assert metrics.expired_leases == 0
    assert metrics.attempts_last_minute == 1
    assert metrics.live_workers == 1
