"""Owner-scoped semantic evidence indexing and hybrid retrieval."""

from __future__ import annotations

import hashlib
from typing import Any
from uuid import UUID

from qdrant_client import models
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.postgres.evidence_tables import pieces, runs
from app.connectors.qdrant import QdrantConnector
from app.domain.evidence import EvidencePiece, normalize
from app.services.language_embeddings import LanguageEmbedder


class EvidenceVectors:
    """Vectors carry references only and are filtered before similarity ranking."""

    def __init__(self, qdrant: QdrantConnector, embedder: LanguageEmbedder) -> None:
        """Keep model provenance in the collection name."""
        self.client = qdrant.client
        self.embedder = embedder
        digest = hashlib.sha256(
            f"{embedder.model_name}\0{embedder.model_version}".encode()
        ).hexdigest()[:24]
        self.collection = f"eagleeye_evidence__{digest}"

    @staticmethod
    def owner_key(owner: str) -> str:
        """Avoid placing account names in Qdrant payloads."""
        return hashlib.sha256(owner.encode()).hexdigest()

    async def index(self, analysis_id: UUID, owner: str, evidence: list[EvidencePiece]) -> None:
        """Upsert deterministic point IDs; replay cannot accumulate duplicate vectors."""
        if not evidence:
            return
        embeddings = await self.embedder.embed_documents(
            [
                " ".join([piece.normalized_text] + [t.get("text", "") for t in piece.translations])
                for piece in evidence
            ]
        )
        if not await self.client.collection_exists(self.collection):
            await self.client.create_collection(
                collection_name=self.collection,
                vectors_config=models.VectorParams(
                    size=embeddings[0].dimension,
                    distance=models.Distance.COSINE,
                ),
            )
            await self.client.create_payload_index(
                collection_name=self.collection,
                field_name="owner_key",
                field_schema=models.PayloadSchemaType.KEYWORD,
            )
        await self.client.upsert(
            collection_name=self.collection,
            wait=True,
            points=[
                models.PointStruct(
                    id=str(piece.evidence_id),
                    vector=embedding.vector.tolist(),
                    payload={
                        "owner_key": self.owner_key(owner),
                        "analysis_id": str(analysis_id),
                        "evidence_id": str(piece.evidence_id),
                    },
                )
                for piece, embedding in zip(evidence, embeddings, strict=True)
            ],
        )

    async def search_ids(self, owner: str, query: str, limit: int) -> list[UUID]:
        """Return candidates from this caller's partition only."""
        if not await self.client.collection_exists(self.collection):
            return []
        vector = await self.embedder.embed_query(query)
        found = await self.client.query_points(
            collection_name=self.collection,
            query=vector.vector.tolist(),
            limit=limit,
            query_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="owner_key",
                        match=models.MatchValue(value=self.owner_key(owner)),
                    )
                ]
            ),
        )
        return [UUID(str(point.id)) for point in found.points]


async def hybrid_search(
    session: AsyncSession,
    owner: str,
    query: str,
    *,
    limit: int,
    semantic_ids: list[UUID] | None = None,
) -> list[dict[str, Any]]:
    """Fuse lexical and semantic ranks, rechecking every result against PostgreSQL."""
    query = normalize(query)
    vector = func.to_tsvector("simple", pieces.c.search_text)
    terms = func.plainto_tsquery("simple", query)
    base = (
        select(pieces.c.evidence_id, pieces.c.body, runs.c.analysis_id, runs.c.submission)
        .join(
            runs,
            pieces.c.analysis_id == runs.c.analysis_id,
        )
        .where(runs.c.owner == owner, runs.c.status.in_(["completed", "partial"]))
    )
    lexical = await session.execute(
        base.where(
            or_(
                vector.op("@@")(terms),
                pieces.c.search_text.icontains(query, autoescape=True),
            )
        )
        .order_by(func.ts_rank(vector, terms).desc(), pieces.c.evidence_id)
        .limit(limit * 3)
    )
    rows = {row["evidence_id"]: dict(row) for row in lexical.mappings()}
    ranks: dict[UUID, float] = {key: 1 / (60 + i) for i, key in enumerate(rows, 1)}
    if semantic_ids:
        allowed = await session.execute(base.where(pieces.c.evidence_id.in_(semantic_ids)))
        for row in allowed.mappings():
            key = row["evidence_id"]
            rows[key] = dict(row)
            ranks[key] = ranks.get(key, 0) + 1 / (61 + semantic_ids.index(key))
    return [
        {
            "analysis_id": rows[key]["analysis_id"],
            "source_id": rows[key]["submission"].get("source_id")
            or rows[key]["submission"].get("source", {}).get("object_id"),
            "source_type": rows[key]["submission"].get("source_type")
            or rows[key]["submission"].get("source", {}).get("object_type"),
            "attributes": rows[key]["submission"].get("attributes", {}),
            "evidence": rows[key]["body"],
            "rank_score": ranks[key],
        }
        for key in sorted(ranks, key=lambda k: ranks[k], reverse=True)[:limit]
    ]
