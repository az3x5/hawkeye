"""Embedding worker.

Consumes enrolment jobs, runs detection and recognition, and stores the
resulting vector. Runs as its own process: the models are heavy, and keeping
them out of the API means a slow or failing model cannot stall a request.
"""

from __future__ import annotations

import asyncio
import logging
import signal

import cv2
import numpy as np

from app.adapters.adaface import EMBEDDING_DIM
from app.adapters.factory import build_detector, build_recognizer
from app.adapters.preprocessing import UInt8Array
from app.connectors.filesystem import FilesystemObjectStore
from app.connectors.postgres import PostgresConnector, SqlAlchemyFaceSampleRepository
from app.connectors.postgres.audit import SqlAlchemyAuditLog
from app.connectors.qdrant import QdrantConnector, QdrantVectorRepository
from app.connectors.redis import RedisConnector, RedisJobQueue
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.domain.jobs import EmbeddingJob
from app.domain.vectors import StoredEmbedding
from app.retention import purge_expired_query_images
from app.services.erasure import reconcile_orphaned_vectors

logger = logging.getLogger(__name__)

#: How long a reserve() call waits before looping, so shutdown stays responsive.
RESERVE_TIMEOUT_SECONDS = 5

#: How often housekeeping runs. Retention is a policy measured in days and
#: orphaned vectors should not arise at all now erasure exists, so hourly is
#: ample and keeps the worker's main job first.
PURGE_INTERVAL_SECONDS = 3600


class JobFailure(Exception):
    """The job cannot be completed and should be recorded as failed."""


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

    def stop(self) -> None:
        """Ask the worker to finish the current job and exit."""
        self._stopping.set()

    async def close(self) -> None:
        """Release every connection."""
        await self._redis.close()
        await self._qdrant.close()
        await self._postgres.close()

    async def run(self) -> None:
        """Process jobs until asked to stop, sweeping expired images between them."""
        while not self._stopping.is_set():
            await self._maybe_purge()
            job = await self._queue.reserve(timeout_seconds=RESERVE_TIMEOUT_SECONDS)
            if job is None:
                continue
            await self.process(job)

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

    async def process(self, job: EmbeddingJob) -> None:
        """Handle one job, recording the outcome in both the queue and the database."""
        try:
            await self._embed(job)
        except JobFailure as exc:
            await self._record_failure(job, str(exc))
        except Exception as exc:  # noqa: BLE001 - a worker must survive one bad job
            logger.exception(
                "unexpected failure processing job",
                extra={"face_sample_uuid": str(job.face_sample_uuid)},
            )
            await self._record_failure(job, f"{type(exc).__name__}: {exc}")
        else:
            async with self._postgres.session() as session:
                await SqlAlchemyFaceSampleRepository(session).mark_processed(job.face_sample_uuid)
            await self._queue.complete(job)
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
            raise JobFailure(f"image {job.image_sha256} is missing from the object store")

        decoded = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        if decoded is None:
            raise JobFailure("the stored image could not be decoded")
        image: UInt8Array = decoded.astype(np.uint8)

        faces = self._detector.detect_and_align(image)
        if not faces:
            raise JobFailure("no face was detected in the image")
        if len(faces) > 1:
            # Which face belongs to the person is an identity question, not a
            # recognition one, so this is refused rather than guessed.
            raise JobFailure(f"{len(faces)} faces were detected; enrolment requires exactly one")

        embedding = self._recognizer.embed(faces[0])
        await self._vectors.upsert(
            StoredEmbedding(
                face_sample_uuid=job.face_sample_uuid,
                person_uuid=job.person_uuid,
                embedding=embedding,
            )
        )

    async def _record_failure(self, job: EmbeddingJob, reason: str) -> None:
        async with self._postgres.session() as session:
            await SqlAlchemyFaceSampleRepository(session).mark_failed(job.face_sample_uuid, reason)
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
