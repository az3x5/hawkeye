"""Qdrant repository for language-document embeddings."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from uuid import UUID, uuid5

import numpy as np
from qdrant_client import models

from app.connectors.qdrant.connector import QdrantConnector
from app.services.language_embeddings import LanguageEmbedding

PAYLOAD_DOCUMENT = "document_uuid"
PAYLOAD_SOURCE = "source"
PAYLOAD_CHUNK = "chunk_index"


def language_collection_name(embedding: LanguageEmbedding) -> str:
    """Return a stable collection name scoped to exact model provenance."""
    provenance = f"{embedding.model}\0{embedding.version}\0{embedding.dimension}"
    suffix = hashlib.sha256(provenance.encode()).hexdigest()[:16]
    return f"hawkeye_language__{suffix}"


@dataclass(frozen=True, slots=True)
class LanguageVectorMatch:
    """A document identifier and its cosine similarity."""

    document_uuid: UUID
    score: float


class QdrantLanguageRepository:
    """Store and query non-biometric language vectors."""

    def __init__(self, connector: QdrantConnector) -> None:
        """Bind the repository to a Qdrant connector."""
        self._connector = connector

    async def ensure_ready(self, embedding: LanguageEmbedding) -> str:
        """Create the provenance-specific collection when absent."""
        name = language_collection_name(embedding)
        client = self._connector.client
        if not await client.collection_exists(name):
            await client.create_collection(
                collection_name=name,
                vectors_config=models.VectorParams(
                    size=embedding.dimension, distance=models.Distance.COSINE
                ),
            )
            await client.create_payload_index(
                collection_name=name,
                field_name=PAYLOAD_SOURCE,
                field_schema=models.PayloadSchemaType.KEYWORD,
            )
            await client.create_payload_index(
                collection_name=name,
                field_name=PAYLOAD_DOCUMENT,
                field_schema=models.PayloadSchemaType.KEYWORD,
            )
        return name

    async def upsert(
        self, document_uuid: UUID, source: str, embeddings: list[LanguageEmbedding]
    ) -> str:
        """Replace all chunk vectors for one document."""
        if not embeddings:
            raise ValueError("at least one language embedding is required")
        first = embeddings[0]
        if any(
            item.model != first.model
            or item.version != first.version
            or item.dimension != first.dimension
            for item in embeddings
        ):
            raise ValueError("all document chunks must share embedding provenance")
        name = await self.ensure_ready(first)
        document_filter = models.Filter(
            must=[
                models.FieldCondition(
                    key=PAYLOAD_DOCUMENT,
                    match=models.MatchValue(value=str(document_uuid)),
                )
            ]
        )
        await self._connector.client.delete(
            collection_name=name,
            points_selector=models.FilterSelector(filter=document_filter),
            wait=True,
        )
        await self._connector.client.upsert(
            collection_name=name,
            points=[
                models.PointStruct(
                    id=str(uuid5(document_uuid, f"chunk:{index}")),
                    vector=embedding.vector.tolist(),
                    payload={
                        PAYLOAD_DOCUMENT: str(document_uuid),
                        PAYLOAD_SOURCE: source,
                        PAYLOAD_CHUNK: index,
                    },
                )
                for index, embedding in enumerate(embeddings)
            ],
            wait=True,
        )
        return name

    async def search(
        self,
        embedding: LanguageEmbedding,
        *,
        limit: int,
        source: str | None = None,
        minimum_score: float | None = None,
    ) -> list[LanguageVectorMatch]:
        """Return nearest documents from the matching model collection."""
        if limit < 1:
            raise ValueError("limit must be positive")
        name = language_collection_name(embedding)
        client = self._connector.client
        if not await client.collection_exists(name):
            return []
        query_filter = None
        if source is not None:
            query_filter = models.Filter(
                must=[
                    models.FieldCondition(
                        key=PAYLOAD_SOURCE,
                        match=models.MatchValue(value=source),
                    )
                ]
            )
        response = await client.query_points(
            collection_name=name,
            query=embedding.vector.tolist(),
            limit=min(limit * 4, 200),
            query_filter=query_filter,
            score_threshold=minimum_score,
            with_payload=True,
        )
        unique: list[LanguageVectorMatch] = []
        seen: set[UUID] = set()
        for point in response.points:
            if point.payload is None or PAYLOAD_DOCUMENT not in point.payload:
                continue
            document_uuid = UUID(str(point.payload[PAYLOAD_DOCUMENT]))
            if document_uuid in seen:
                continue
            seen.add(document_uuid)
            unique.append(
                LanguageVectorMatch(
                    document_uuid=document_uuid,
                    score=float(np.clip(point.score, -1.0, 1.0)),
                )
            )
            if len(unique) == limit:
                break
        return unique
