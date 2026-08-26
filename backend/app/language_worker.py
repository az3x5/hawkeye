"""Background worker that embeds persisted language documents."""

from __future__ import annotations

import asyncio
import logging
import signal

from app.connectors.postgres import (
    PostgresConnector,
    SqlAlchemyLanguageDocumentRepository,
)
from app.connectors.qdrant import QdrantConnector, QdrantLanguageRepository
from app.connectors.redis import RedisConnector, RedisLanguageJobQueue
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.domain.jobs import LanguageEmbeddingJob
from app.services.language_embeddings import MultilingualE5Embedder, chunk_text

logger = logging.getLogger(__name__)
RESERVE_TIMEOUT_SECONDS = 5


class LanguageEmbeddingWorker:
    """Turn queued normalized text into versioned Qdrant vectors."""

    def __init__(self, settings: Settings) -> None:
        """Build connectors and load the configured embedding model once."""
        if not settings.language_embedding_model:
            raise ValueError("FACEID_LANGUAGE_EMBEDDING_MODEL is required")
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
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        """Verify dependencies before reserving jobs."""
        await self._postgres.ping()
        await self._qdrant.ping()
        await self._redis.ping()
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
        """Process jobs until stopped."""
        while not self._stopping.is_set():
            job = await self._queue.reserve(timeout_seconds=RESERVE_TIMEOUT_SECONDS)
            if job is not None:
                await self.process(job)

    async def process(self, job: LanguageEmbeddingJob) -> None:
        """Embed one document while preserving failure evidence."""
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
            async with self._postgres.session() as session:
                repository = SqlAlchemyLanguageDocumentRepository(session)
                if await repository.get(job.document_uuid) is not None:
                    await repository.mark_failed(job.document_uuid, reason)
            await self._queue.fail(job, reason)
        else:
            await self._queue.complete(job)
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
