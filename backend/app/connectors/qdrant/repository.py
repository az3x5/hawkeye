"""Vector repository backed by Qdrant."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from uuid import UUID

import numpy as np
from qdrant_client import models

from app.connectors.qdrant.connector import QdrantConnector
from app.connectors.qdrant.naming import collection_name
from app.domain.recognition import EmbeddingProvenance, FaceEmbedding
from app.domain.vectors import StoredEmbedding, VectorMatch

logger = logging.getLogger(__name__)

#: Payload keys. Identifiers only — no images, no names, no free text.
PAYLOAD_PERSON = "person_uuid"
PAYLOAD_SAMPLE = "face_sample_uuid"
PAYLOAD_MODEL_NAME = "model_name"
PAYLOAD_MODEL_VERSION = "model_version"
PAYLOAD_PREPROCESSING = "preprocessing_version"


class QdrantVectorRepository:
    """``VectorRepository`` over Qdrant collections, one per provenance."""

    def __init__(self, connector: QdrantConnector) -> None:
        """Bind the repository to a connector."""
        self._connector = connector

    async def ensure_ready(self, provenance: EmbeddingProvenance, dimension: int) -> None:
        """Create the collection for this provenance if it does not exist."""
        name = collection_name(provenance)
        client = self._connector.client
        if await client.collection_exists(name):
            return
        await client.create_collection(
            collection_name=name,
            vectors_config=models.VectorParams(size=dimension, distance=models.Distance.COSINE),
        )
        # Person lookups and deletions filter on this key.
        await client.create_payload_index(
            collection_name=name,
            field_name=PAYLOAD_PERSON,
            field_schema=models.PayloadSchemaType.KEYWORD,
        )
        logger.info(
            "created vector collection",
            extra={"collection": name, "dimension": dimension},
        )

    async def upsert(self, stored: StoredEmbedding) -> None:
        """Store (or replace) the embedding for one face sample."""
        await self.upsert_many([stored])

    async def upsert_many(self, stored: Sequence[StoredEmbedding]) -> None:
        """Store several embeddings sharing one provenance."""
        if not stored:
            return

        provenance = stored[0].embedding.provenance
        for item in stored:
            if not item.embedding.provenance.matches(provenance):
                raise ValueError(
                    "all embeddings in one write must share a provenance; got "
                    f"{provenance} and {item.embedding.provenance}"
                )

        await self.ensure_ready(provenance, stored[0].embedding.dimension)
        points = [
            models.PointStruct(
                # The sample's own uuid is the point id, so re-writing the same
                # sample replaces it rather than accumulating duplicates.
                id=str(item.face_sample_uuid),
                vector=item.embedding.vector.tolist(),
                payload={
                    PAYLOAD_PERSON: str(item.person_uuid),
                    PAYLOAD_SAMPLE: str(item.face_sample_uuid),
                    PAYLOAD_MODEL_NAME: provenance.model_name,
                    PAYLOAD_MODEL_VERSION: provenance.model_version,
                    PAYLOAD_PREPROCESSING: provenance.preprocessing_version,
                },
            )
            for item in stored
        ]
        await self._connector.client.upsert(
            collection_name=collection_name(provenance), points=points, wait=True
        )

    async def search(
        self, embedding: FaceEmbedding, *, limit: int, exclude_person: UUID | None = None
    ) -> list[VectorMatch]:
        """Return the closest embeddings, most similar first."""
        if limit < 1:
            raise ValueError(f"limit must be at least 1, got {limit}")

        name = collection_name(embedding.provenance)
        client = self._connector.client
        if not await client.collection_exists(name):
            return []

        query_filter = None
        if exclude_person is not None:
            query_filter = models.Filter(
                must_not=[
                    models.FieldCondition(
                        key=PAYLOAD_PERSON,
                        match=models.MatchValue(value=str(exclude_person)),
                    )
                ]
            )

        response = await client.query_points(
            collection_name=name,
            query=embedding.vector.tolist(),
            limit=limit,
            query_filter=query_filter,
            with_payload=True,
        )
        return [
            VectorMatch(
                face_sample_uuid=UUID(point.payload[PAYLOAD_SAMPLE]),
                person_uuid=UUID(point.payload[PAYLOAD_PERSON]),
                # Cosine distance in Qdrant is already a similarity; clamp only
                # to absorb floating-point drift beyond the valid range.
                score=float(np.clip(point.score, -1.0, 1.0)),
            )
            for point in response.points
            if point.payload is not None
        ]

    async def get(
        self, face_sample_uuid: UUID, provenance: EmbeddingProvenance
    ) -> FaceEmbedding | None:
        """Return one stored embedding, or None."""
        name = collection_name(provenance)
        client = self._connector.client
        if not await client.collection_exists(name):
            return None

        points = await client.retrieve(
            collection_name=name, ids=[str(face_sample_uuid)], with_vectors=True
        )
        if not points:
            return None

        vector = points[0].vector
        if not isinstance(vector, list):
            raise TypeError(f"expected a dense vector for {face_sample_uuid}, got {type(vector)}")
        return FaceEmbedding(vector=np.asarray(vector, dtype=np.float32), provenance=provenance)

    async def delete_person(self, person_uuid: UUID, provenance: EmbeddingProvenance) -> int:
        """Remove every embedding belonging to a person."""
        name = collection_name(provenance)
        client = self._connector.client
        if not await client.collection_exists(name):
            return 0

        condition = models.Filter(
            must=[
                models.FieldCondition(
                    key=PAYLOAD_PERSON, match=models.MatchValue(value=str(person_uuid))
                )
            ]
        )
        before = await client.count(collection_name=name, count_filter=condition, exact=True)
        await client.delete(
            collection_name=name,
            points_selector=models.FilterSelector(filter=condition),
            wait=True,
        )
        return int(before.count)
