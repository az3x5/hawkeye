"""Vector storage contracts.

The vector store holds embeddings and answers nearest-neighbour queries. It
returns similarity scores and identifiers — never a decision about who someone
is, and never a probability.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from uuid import UUID

from app.domain.recognition import EmbeddingProvenance, FaceEmbedding


@dataclass(frozen=True, slots=True)
class StoredEmbedding:
    """An embedding together with the sample and person it belongs to."""

    face_sample_uuid: UUID
    person_uuid: UUID
    embedding: FaceEmbedding


@dataclass(frozen=True, slots=True)
class VectorMatch:
    """One nearest-neighbour result.

    ``score`` is the raw cosine similarity the index returned. It is not
    calibrated, not a probability, and carries no claim that the two faces
    belong to the same person.
    """

    face_sample_uuid: UUID
    person_uuid: UUID
    score: float

    def __post_init__(self) -> None:
        """Validate the similarity range."""
        if not -1.0 <= self.score <= 1.0:
            raise ValueError(f"cosine similarity must be in [-1, 1], got {self.score}")


@runtime_checkable
class VectorRepository(Protocol):
    """Persistence and search of face embeddings."""

    async def ensure_ready(self, provenance: EmbeddingProvenance, dimension: int) -> None:
        """Make sure the index for this provenance exists and has the right shape."""
        ...

    async def upsert(self, stored: StoredEmbedding) -> None:
        """Store (or replace) the embedding for one face sample.

        Keyed by ``face_sample_uuid`` so repeating the same write converges
        rather than accumulating duplicates.
        """
        ...

    async def upsert_many(self, stored: Sequence[StoredEmbedding]) -> None:
        """Store several embeddings sharing one provenance, in a single call."""
        ...

    async def search(
        self, embedding: FaceEmbedding, *, limit: int, exclude_person: UUID | None = None
    ) -> list[VectorMatch]:
        """Return the closest embeddings, most similar first.

        Deliberately has no score cut-off: filtering by similarity is an
        identity decision, and the threshold that would express it belongs to
        the layer that owns such decisions.
        """
        ...

    async def get(
        self, face_sample_uuid: UUID, provenance: EmbeddingProvenance
    ) -> FaceEmbedding | None:
        """Return one stored embedding, or None."""
        ...

    async def delete_person(self, person_uuid: UUID, provenance: EmbeddingProvenance) -> int:
        """Remove a person's embeddings under one provenance. Returns the count."""
        ...

    async def delete_person_everywhere(self, person_uuid: UUID) -> int:
        """Remove a person's embeddings under every provenance.

        Erasure cannot be scoped to the current model: embeddings made under an
        older model or preprocessing would otherwise survive the deletion.
        """
        ...

    async def collections(self) -> list[str]:
        """Every collection holding embeddings, across all provenances."""
        ...

    async def person_uuids(self, collection: str, *, batch: int = 512) -> set[UUID]:
        """Every person referenced by a collection's payloads."""
        ...
