"""Connector-owned consumer for durable PostgreSQL jobs."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.postgres.connector import PostgresConnector
from app.connectors.postgres.jobs import SqlAlchemyProcessingJobRepository
from app.domain.processing import JobErrorCode, JobReservation, JobStatus, ProcessingJob


class PostgresJobConsumer:
    """Claim and finish one pipeline's jobs across short transactions."""

    def __init__(
        self,
        connector: PostgresConnector,
        *,
        pipeline: str,
        worker_id: str,
        lease_seconds: int,
        retry_base_seconds: int,
        retry_max_seconds: int,
    ) -> None:
        """Bind the connector and bounded lease/retry policy."""
        self._connector = connector
        self.pipeline = pipeline
        self.worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._retry_base_seconds = retry_base_seconds
        self._retry_max_seconds = retry_max_seconds

    def _repository(self, session: AsyncSession) -> SqlAlchemyProcessingJobRepository:
        return SqlAlchemyProcessingJobRepository(
            session,
            retry_base_seconds=self._retry_base_seconds,
            retry_max_seconds=self._retry_max_seconds,
        )

    async def reserve(self) -> JobReservation | None:
        """Claim one job, committing its lease before inference starts."""
        async with self._connector.session() as session:
            return await self._repository(session).claim(
                self.pipeline,
                worker_id=self.worker_id,
                lease_seconds=self._lease_seconds,
            )

    async def complete(self, reservation: JobReservation) -> None:
        """Commit successful completion under the reservation token."""
        async with self._connector.session() as session:
            await self._repository(session).complete(reservation)

    async def fail(
        self,
        reservation: JobReservation,
        *,
        error_code: JobErrorCode,
        detail: str,
        retryable: bool,
    ) -> JobStatus:
        """Commit a failed attempt and its retry/dead-letter decision."""
        async with self._connector.session() as session:
            return await self._repository(session).fail(
                reservation,
                error_code=error_code,
                detail=detail,
                retryable=retryable,
            )

    async def enqueue_legacy(self, job: ProcessingJob) -> None:
        """Idempotently persist one job drained from a pre-M1 Redis list."""
        async with self._connector.session() as session:
            await self._repository(session).enqueue(job)

    async def heartbeat(self) -> None:
        """Report idle presence without extending any lease."""
        async with self._connector.session() as session:
            await self._repository(session).heartbeat(
                self.worker_id, self.pipeline, current_job_uuid=None
            )
