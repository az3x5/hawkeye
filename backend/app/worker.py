"""Embedding worker.

Consumes enrolment jobs, runs detection and recognition, and stores the
resulting vector. Runs as its own process: the models are heavy, and keeping
them out of the API means a slow or failing model cannot stall a request.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket

import cv2
import numpy as np

from app.adapters.adaface import EMBEDDING_DIM
from app.adapters.factory import build_detector, build_recognizer
from app.adapters.preprocessing import UInt8Array
from app.connectors.filesystem import FilesystemObjectStore
from app.connectors.postgres import (
    PostgresConnector,
    PostgresJobConsumer,
    SqlAlchemyEmbeddingMetadataRepository,
    SqlAlchemyFaceSampleRepository,
)
from app.connectors.postgres.audit import SqlAlchemyAuditLog
from app.connectors.qdrant import QdrantConnector, QdrantVectorRepository, collection_name
from app.connectors.redis import RedisConnector, RedisJobQueue
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.domain.jobs import EmbeddingJob, JobQueueError
from app.domain.processing import (
    JobErrorCode,
    JobLeaseError,
    JobReservation,
    JobStatus,
    ProcessingJob,
)
from app.domain.vectors import StoredEmbedding
from app.retention import purge_expired_query_images
from app.services.erasure import reconcile_orphaned_images, reconcile_orphaned_vectors
from app.services.processing import (
    FACE_EMBEDDING_PIPELINE,
    FACE_EMBEDDING_PIPELINE_VERSION,
)

logger = logging.getLogger(__name__)

#: How long a reserve() call waits before looping, so shutdown stays responsive.
RESERVE_TIMEOUT_SECONDS = 5

#: How often housekeeping runs. Retention is a policy measured in days and
#: orphaned vectors should not arise at all now erasure exists, so hourly is
#: ample and keeps the worker's main job first.
PURGE_INTERVAL_SECONDS = 3600


class JobFailure(Exception):
    """The job cannot be completed and should be recorded as failed."""

    def __init__(self, message: str, error_code: JobErrorCode) -> None:
        """Create a permanent pipeline failure with a stable code."""
        super().__init__(message)
        self.error_code = error_code


class EmbeddingWorker:
    """Turns enrolled face samples into stored embeddings."""

    def __init__(self, settings: Settings) -> None:
        """Build the worker's adapters and connectors."""
        self._settings = settings
        self._detector = build_detector(settings)
        self._recognizer = build_recognizer(settings)
        self._postgres = PostgresConnector(str(settings.postgres_dsn))
        self._qdrant = QdrantConnector(settings.qdrant_url, api_key=settings.qdrant_api_key)
        self._redis = RedisConnector(str(settings.redis_dsn))
        self._objects = FilesystemObjectStore(settings.object_store_root)
        self._queue = RedisJobQueue(self._redis)
        self._vectors = QdrantVectorRepository(self._qdrant)
        self._stopping = asyncio.Event()
        self._last_purge = 0.0
        worker_id = f"eagleeye:face:{socket.gethostname()}:{os.getpid()}"
        self._jobs = PostgresJobConsumer(
            self._postgres,
            pipeline=FACE_EMBEDDING_PIPELINE,
            worker_id=worker_id,
            lease_seconds=settings.job_lease_seconds,
            retry_base_seconds=settings.job_retry_base_seconds,
            retry_max_seconds=settings.job_retry_max_seconds,
        )
        self._poll_interval = settings.job_poll_interval_seconds

    async def start(self) -> None:
        """Load models and verify every dependency before taking work."""
        self._detector.warmup()
        self._recognizer.warmup()
        await self._postgres.ping()
        await self._qdrant.ping()
        await self._redis.ping()
        await self._objects.ping()
        await self._vectors.ensure_ready(self._recognizer.provenance, EMBEDDING_DIM)
        logger.info(
            "embedding worker ready",
            extra={
                "detector": self._detector.model_name,
                "recognizer": self._recognizer.model_name,
                "model_version": self._recognizer.model_version,
            },
        )
        await self._migrate_legacy_jobs()

    def stop(self) -> None:
        """Ask the worker to finish the current job and exit."""
        self._stopping.set()

    async def close(self) -> None:
        """Release every connection."""
        await self._redis.close()
        await self._qdrant.close()
        await self._postgres.close()

    async def run(self) -> None:
        """Process durable jobs until asked to stop."""
        while not self._stopping.is_set():
            await self._maybe_purge()
            reservation = await self._jobs.reserve()
            if reservation is None:
                await asyncio.sleep(self._poll_interval)
                continue
            await self._process_reservation(reservation)

    async def _process_reservation(self, reservation: JobReservation) -> None:
        """Validate the durable envelope before entering the face pipeline."""
        try:
            payload = reservation.job.payload
            if not all(
                isinstance(key, str) and isinstance(value, str) for key, value in payload.items()
            ):
                raise JobQueueError("face job payload values must be strings")
            job = EmbeddingJob.from_payload(payload)
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
                    pipeline=FACE_EMBEDDING_PIPELINE,
                    pipeline_version=FACE_EMBEDDING_PIPELINE_VERSION,
                    subject_type="face_sample",
                    subject_uuid=job.face_sample_uuid,
                    idempotency_key=(f"{job.face_sample_uuid}:{FACE_EMBEDDING_PIPELINE_VERSION}"),
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
                "legacy face jobs migrated",
                extra={"recovered": recovered, "migrated": migrated},
            )

    async def _maybe_purge(self) -> None:
        """Run housekeeping if it is due.

        Failures are logged and swallowed: housekeeping must never stop the
        worker from embedding faces.
        """
        now = asyncio.get_running_loop().time()
        if now - self._last_purge < PURGE_INTERVAL_SECONDS:
            return
        self._last_purge = now
        try:
            async with self._postgres.session() as session:
                await purge_expired_query_images(
                    session=session,
                    objects=self._objects,
                    audit=SqlAlchemyAuditLog(session),
                    retention_days=self._settings.query_image_retention_days,
                )
        except Exception:  # noqa: BLE001 - logged; the sweep retries next hour
            logger.exception("retention sweep failed")

        try:
            async with self._postgres.session() as session:
                # Erasure keeps the stores in step; this catches anything that
                # fell out of step before it existed, or through a partial
                # failure. A face with no person attached is still searchable.
                await reconcile_orphaned_vectors(
                    session=session,
                    vectors=self._vectors,
                    audit=SqlAlchemyAuditLog(session),
                )
        except Exception:  # noqa: BLE001 - logged; the sweep retries next hour
            logger.exception("orphaned vector reconciliation failed")

        try:
            async with self._postgres.session() as session:
                await reconcile_orphaned_images(
                    session=session,
                    objects=self._objects,
                    audit=SqlAlchemyAuditLog(session),
                )
        except Exception:  # noqa: BLE001 - logged; the sweep retries next hour
            logger.exception("orphaned image reconciliation failed")

    async def process(
        self, job: EmbeddingJob, *, reservation: JobReservation | None = None
    ) -> None:
        """Handle one job and record its durable or legacy outcome."""
        try:
            await self._embed(job)
        except JobFailure as exc:
            await self._record_failure(
                job,
                str(exc),
                error_code=exc.error_code,
                retryable=False,
                reservation=reservation,
            )
        except Exception as exc:  # noqa: BLE001 - a worker must survive one bad job
            logger.exception(
                "unexpected failure processing job",
                extra={"face_sample_uuid": str(job.face_sample_uuid)},
            )
            await self._record_failure(
                job,
                f"{type(exc).__name__}: {exc}",
                error_code=JobErrorCode.INTERNAL_ERROR,
                retryable=True,
                reservation=reservation,
            )
        else:
            async with self._postgres.session() as session:
                await SqlAlchemyFaceSampleRepository(session).mark_processed(job.face_sample_uuid)
            try:
                if reservation is None:
                    await self._queue.complete(job)
                else:
                    await self._jobs.complete(reservation)
            except JobLeaseError:
                logger.warning(
                    "face job completed after its lease was lost",
                    extra={"face_sample_uuid": str(job.face_sample_uuid)},
                )
            logger.info(
                "face sample embedded",
                extra={
                    "face_sample_uuid": str(job.face_sample_uuid),
                    "person_uuid": str(job.person_uuid),
                },
            )

    async def _embed(self, job: EmbeddingJob) -> None:
        data = await self._objects.get(job.image_sha256)
        if data is None:
            raise JobFailure(
                f"image {job.image_sha256} is missing from the object store",
                JobErrorCode.SOURCE_UNAVAILABLE,
            )

        decoded = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        if decoded is None:
            raise JobFailure("the stored image could not be decoded", JobErrorCode.INVALID_IMAGE)
        image: UInt8Array = decoded.astype(np.uint8)

        faces = self._detector.detect_and_align(image)
        if not faces:
            raise JobFailure("no face was detected in the image", JobErrorCode.NO_FACE)
        if len(faces) > 1:
            # Which face belongs to the person is an identity question, not a
            # recognition one, so this is refused rather than guessed.
            raise JobFailure(
                f"{len(faces)} faces were detected; enrolment requires exactly one",
                JobErrorCode.MULTIPLE_FACES,
            )

        embedding = self._recognizer.embed(faces[0])
        await self._vectors.upsert(
            StoredEmbedding(
                face_sample_uuid=job.face_sample_uuid,
                person_uuid=job.person_uuid,
                embedding=embedding,
            )
        )

        # The vector lives in the vector store; this row is what attributes it
        # to a model and preprocessing version from the metadata store, and
        # what makes a model migration observable there.
        async with self._postgres.session() as session:
            await SqlAlchemyEmbeddingMetadataRepository(session).record(
                face_sample_uuid=job.face_sample_uuid,
                provenance=embedding.provenance,
                collection=collection_name(embedding.provenance),
                dimension=embedding.dimension,
            )

    async def _record_failure(
        self,
        job: EmbeddingJob,
        reason: str,
        *,
        error_code: JobErrorCode,
        retryable: bool,
        reservation: JobReservation | None,
    ) -> None:
        result: JobStatus | None = None
        if reservation is not None:
            result = await self._jobs.fail(
                reservation,
                error_code=error_code,
                detail=reason,
                retryable=retryable,
            )
        if result is JobStatus.RETRY:
            return
        async with self._postgres.session() as session:
            await SqlAlchemyFaceSampleRepository(session).mark_failed(job.face_sample_uuid, reason)
        if reservation is None:
            await self._queue.fail(job, reason)


async def main() -> None:
    """Entry point for the worker process."""
    settings = get_settings()
    configure_logging()
    worker = EmbeddingWorker(settings)

    loop = asyncio.get_running_loop()
    for received in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(received, worker.stop)

    await worker.start()
    try:
        await worker.run()
    finally:
        await worker.close()
        logger.info("embedding worker stopped")


if __name__ == "__main__":
    asyncio.run(main())
