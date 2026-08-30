"""Document ingestion and semantic search orchestration."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.connectors.postgres.language import SqlAlchemyLanguageDocumentRepository
from app.connectors.qdrant.language import QdrantLanguageRepository
from app.domain.jobs import LanguageEmbeddingJob, LanguageJobSubmitter, ProcessingState
from app.domain.language import LanguageDocument, LanguageSearchHit
from app.services.language import normalize_text
from app.services.language_embeddings import LanguageEmbedder


@dataclass(frozen=True, slots=True)
class DocumentSubmission:
    """Bounded data used to create a searchable document."""

    title: str
    source: str
    text: str
    attributes: dict[str, Any]


class LanguageDocumentService:
    """Persist text before reliably scheduling expensive embedding work."""

    def __init__(
        self,
        documents: SqlAlchemyLanguageDocumentRepository,
        queue: LanguageJobSubmitter,
    ) -> None:
        """Bind document metadata and its reliable queue."""
        self._documents = documents
        self._queue = queue

    async def submit(self, request: DocumentSubmission) -> tuple[LanguageDocument, bool]:
        """Normalize and idempotently enqueue one document."""
        normalized = normalize_text(request.text)
        digest = hashlib.sha256(normalized.normalized.encode("utf-8")).hexdigest()
        document = LanguageDocument(
            title=request.title.strip(),
            source=request.source.strip(),
            original_text=request.text,
            normalized_text=normalized.normalized,
            primary_script=normalized.primary_script,
            content_sha256=digest,
            attributes=request.attributes,
        )
        stored, created = await self._documents.add(document)
        if stored.processing_state is ProcessingState.PENDING:
            await self._queue.enqueue(LanguageEmbeddingJob(stored.document_uuid))
        return stored, created

    async def get(self, document_uuid: UUID) -> LanguageDocument | None:
        """Return one document by UUID-compatible identifier."""
        return await self._documents.get(document_uuid)


class LanguageSearchService:
    """Embed a query, retrieve neighbours, then hydrate trusted metadata."""

    def __init__(
        self,
        documents: SqlAlchemyLanguageDocumentRepository,
        vectors: QdrantLanguageRepository,
        embedder: LanguageEmbedder,
    ) -> None:
        """Bind the document, vector, and query-embedding collaborators."""
        self._documents = documents
        self._vectors = vectors
        self._embedder = embedder

    async def search(
        self,
        query: str,
        *,
        limit: int,
        source: str | None,
        minimum_score: float | None,
    ) -> tuple[list[LanguageSearchHit], str, str]:
        """Return similarity-ranked documents and embedding provenance."""
        normalized = normalize_text(query).normalized
        embedding = await self._embedder.embed_query(normalized)
        matches = await self._vectors.search(
            embedding,
            limit=limit,
            source=source,
            minimum_score=minimum_score,
        )
        documents = await self._documents.get_many([match.document_uuid for match in matches])
        hits = [
            LanguageSearchHit(
                document_uuid=match.document_uuid,
                title=documents[match.document_uuid].title,
                source=documents[match.document_uuid].source,
                text=documents[match.document_uuid].original_text,
                primary_script=documents[match.document_uuid].primary_script,
                score=match.score,
                attributes=documents[match.document_uuid].attributes,
            )
            for match in matches
            if match.document_uuid in documents
        ]
        return hits, embedding.model, embedding.version
