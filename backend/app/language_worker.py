"""Background worker that embeds persisted language documents."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket

from app.connectors.postgres import (
    PostgresConnector,
    PostgresJobConsumer,
    SqlAlchemyLanguageDocumentRepository,
)
from app.connectors.qdrant import QdrantConnector, QdrantLanguageRepository
from app.connectors.redis import RedisConnector, RedisLanguageJobQueue
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.domain.jobs import JobQueueError, LanguageEmbeddingJob
from app.domain.processing import (
    JobErrorCode,
    JobLeaseError,
    JobReservation,
    JobStatus,
    ProcessingJob,
)
from app.services.language_embeddings import MultilingualE5Embedder, chunk_text
from app.services.processing import (
    LANGUAGE_EMBEDDING_PIPELINE,
    LANGUAGE_EMBEDDING_PIPELINE_VERSION,
)

logger = logging.getLogger(__name__)
RESERVE_TIMEOUT_SECONDS = 5


class LanguageEmbeddingWorker:
    """Turn queued normalized text into versioned Qdrant vectors."""

    def __init__(self, settings: Settings) -> None:
        """Build connectors and load the configured embedding model once."""
        if not settings.language_embedding_model:
            raise ValueError("FACEID_LANGUAGE_EMBEDDING_MODEL is required")
        self._settings = settings
        self._postgres = PostgresConnector(str(settings.postgres_dsn))
        self._qdrant = QdrantConnector(settings.qdrant_url, api_key=settings.qdrant_api_key)
        self._redis = RedisConnector(str(settings.redis_dsn))
        self._queue = RedisLanguageJobQueue(self._redis)
        self._vectors = QdrantLanguageRepository(self._qdrant)
        self._embedder = MultilingualE5Embedder(
            settings.language_embedding_model,
            model_version=settings.language_embedding_version,
            device=settings.language_embedding_device,
            batch_size=settings.language_embedding_batch_size,
            max_tokens=settings.language_embedding_max_tokens,
        )
        worker_id = f"eagleeye:language:{socket.gethostname()}:{os.getpid()}"
        self._jobs = PostgresJobConsumer(
            self._postgres,
            pipeline=LANGUAGE_EMBEDDING_PIPELINE,
            worker_id=worker_id,
            lease_seconds=settings.job_lease_seconds,
            retry_base_seconds=settings.job_retry_base_seconds,
            retry_max_seconds=settings.job_retry_max_seconds,
        )
        self._poll_interval = settings.job_poll_interval_seconds
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        """Verify dependencies before reserving jobs."""
        await self._postgres.ping()
        await self._qdrant.ping()
        await self._redis.ping()
        await self._migrate_legacy_jobs()
        logger.info(
            "language embedding worker ready",
            extra={
                "model": self._embedder.model_name,
                "version": self._embedder.model_version,
            },
        )

    def stop(self) -> None:
        """Request graceful shutdown."""
        self._stopping.set()

    async def close(self) -> None:
        """Release connectors."""
        await self._redis.close()
        await self._qdrant.close()
        await self._postgres.close()

    async def run(self) -> None:
        """Process durable language jobs until stopped."""
        while not self._stopping.is_set():
            reservation = await self._jobs.reserve()
            if reservation is None:
                await asyncio.sleep(self._poll_interval)
                continue
            await self._process_reservation(reservation)

    async def _process_reservation(self, reservation: JobReservation) -> None:
        """Validate the durable envelope before embedding text."""
        try:
            payload = reservation.job.payload
            if not all(
                isinstance(key, str) and isinstance(value, str) for key, value in payload.items()
            ):
                raise JobQueueError("language job payload values must be strings")
            job = LanguageEmbeddingJob.from_payload(payload)
        except JobQueueError as exc:
            await self._jobs.fail(
                reservation,
                error_code=JobErrorCode.INVALID_MEDIA,
                detail=str(exc),
                retryable=False,
            )
            return
        await self.process(job, reservation=reservation)

    async def _migrate_legacy_jobs(self) -> None:
        """Drain pre-M1 Redis work into PostgreSQL without losing reservations."""
        recovered = await self._queue.recover_in_flight()
        migrated = 0
        while True:
            job = await self._queue.reserve_nowait()
            if job is None:
                break
            await self._jobs.enqueue_legacy(
                ProcessingJob(
                    pipeline=LANGUAGE_EMBEDDING_PIPELINE,
                    pipeline_version=LANGUAGE_EMBEDDING_PIPELINE_VERSION,
                    subject_type="language_document",
                    subject_uuid=job.document_uuid,
                    idempotency_key=(f"{job.document_uuid}:{LANGUAGE_EMBEDDING_PIPELINE_VERSION}"),
                    payload=job.to_payload(),
                    max_attempts=self._settings.job_max_attempts,
                    queued_at=job.enqueued_at,
                    available_at=job.enqueued_at,
                )
            )
            await self._queue.complete(job)
            migrated += 1
        if recovered or migrated:
            logger.info(
                "legacy language jobs migrated",
                extra={"recovered": recovered, "migrated": migrated},
            )

    async def process(
        self, job: LanguageEmbeddingJob, *, reservation: JobReservation | None = None
    ) -> None:
        """Embed one document while preserving durable failure evidence."""
        try:
            async with self._postgres.session() as session:
                document = await SqlAlchemyLanguageDocumentRepository(session).get(
                    job.document_uuid
                )
            if document is None:
                raise KeyError(f"no language document {job.document_uuid}")
            chunks = chunk_text(document.normalized_text)
            embeddings = await self._embedder.embed_documents(chunks)
            embedding = embeddings[0]
            collection = await self._vectors.upsert(
                document.document_uuid, document.source, embeddings
            )
            async with self._postgres.session() as session:
                await SqlAlchemyLanguageDocumentRepository(session).mark_processed(
                    job.document_uuid,
                    model=embedding.model,
                    version=embedding.version,
                    collection=collection,
                )
        except Exception as exc:  # noqa: BLE001 - one document must not stop the worker
            logger.exception(
                "language document embedding failed",
                extra={"document_uuid": str(job.document_uuid)},
            )
            reason = f"{type(exc).__name__}: {exc}"
            retryable = not isinstance(exc, KeyError)
            result: JobStatus | None = None
            if reservation is not None:
                result = await self._jobs.fail(
                    reservation,
                    error_code=(
                        JobErrorCode.INTERNAL_ERROR
                        if retryable
                        else JobErrorCode.SOURCE_UNAVAILABLE
                    ),
                    detail=reason,
                    retryable=retryable,
                )
            if result is not JobStatus.RETRY:
                async with self._postgres.session() as session:
                    repository = SqlAlchemyLanguageDocumentRepository(session)
                    if await repository.get(job.document_uuid) is not None:
                        await repository.mark_failed(job.document_uuid, reason)
            if reservation is None:
                await self._queue.fail(job, reason)
        else:
            try:
                if reservation is None:
                    await self._queue.complete(job)
                else:
                    await self._jobs.complete(reservation)
            except JobLeaseError:
                logger.warning(
                    "language job completed after its lease was lost",
                    extra={"document_uuid": str(job.document_uuid)},
                )
            logger.info(
                "language document embedded",
                extra={
                    "document_uuid": str(job.document_uuid),
                    "chunks": len(embeddings),
                },
            )


async def main() -> None:
    """Run the language worker until SIGINT or SIGTERM."""
    configure_logging()
    worker = LanguageEmbeddingWorker(get_settings())
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, worker.stop)
    try:
        await worker.start()
        await worker.run()
    finally:
        await worker.close()


if __name__ == "__main__":
    asyncio.run(main())
