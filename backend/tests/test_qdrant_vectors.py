"""Integration tests for the Qdrant connector and vector repository.

These run against a real Qdrant. Set ``FACEID_TEST_QDRANT_URL`` to enable them
(the dev compose overlay publishes it on loopback); without it they are skipped
rather than silently passing.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import numpy as np
import numpy.typing as npt
import pytest
import pytest_asyncio

from app.connectors.qdrant import QdrantConnector, QdrantVectorRepository, collection_name
from app.domain.recognition import EmbeddingProvenance, FaceEmbedding
from app.domain.vectors import StoredEmbedding, VectorMatch, VectorRepository

QDRANT_URL = os.environ.get("FACEID_TEST_QDRANT_URL")

pytestmark = pytest.mark.skipif(
    QDRANT_URL is None,
    reason="set FACEID_TEST_QDRANT_URL to run Qdrant integration tests",
)

DIMENSION = 512


#: Deliberately not a real model name. These tests create and drop whole
#: collections, and the collection name is derived from the provenance, so
#: sharing a model name with the real recogniser would mean dropping the
#: collection other tests are using.
TEST_MODEL_NAME = "test_only_vector_model"


def _provenance(**overrides: str) -> EmbeddingProvenance:
    values = {
        "model_name": TEST_MODEL_NAME,
        "model_version": "a" * 64,
        "preprocessing_version": "scrfd-letterbox-arcface112-v1",
    }
    values.update(overrides)
    return EmbeddingProvenance(**values)  # type: ignore[arg-type]


def _vector(seed: int) -> npt.NDArray[np.float32]:
    rng = np.random.default_rng(seed)
    raw = rng.normal(size=DIMENSION).astype(np.float32)
    normalised: npt.NDArray[np.float32] = (raw / np.linalg.norm(raw)).astype(np.float32)
    return normalised


def _embedding(seed: int, provenance: EmbeddingProvenance | None = None) -> FaceEmbedding:
    return FaceEmbedding(vector=_vector(seed), provenance=provenance or _provenance())


def _stored(
    seed: int, person: UUID, provenance: EmbeddingProvenance | None = None
) -> StoredEmbedding:
    return StoredEmbedding(
        face_sample_uuid=uuid4(),
        person_uuid=person,
        embedding=_embedding(seed, provenance),
    )


@pytest_asyncio.fixture
async def connector() -> AsyncIterator[QdrantConnector]:
    assert QDRANT_URL is not None
    connector = QdrantConnector(QDRANT_URL)
    yield connector
    # Drop every collection this run created, so tests never see each other's data.
    collections = await connector.client.get_collections()
    for description in collections.collections:
        # Only this suite's own collections: dropping anything else would
        # delete data another test is relying on.
        if description.name.startswith(f"face_embeddings__{TEST_MODEL_NAME}"):
            await connector.client.delete_collection(description.name)
    await connector.close()


@pytest_asyncio.fixture
async def repository(connector: QdrantConnector) -> QdrantVectorRepository:
    return QdrantVectorRepository(connector)


class TestConnector:
    async def test_satisfies_the_storage_connector_contract(
        self, connector: QdrantConnector
    ) -> None:
        assert connector.provider == "qdrant"
        await connector.ping()

    async def test_ping_raises_when_unreachable(self) -> None:
        dead = QdrantConnector("http://127.0.0.1:1", timeout=1)
        with pytest.raises(Exception):  # noqa: B017 - client-specific transport error
            await dead.ping()
        await dead.close()


class TestCollectionNaming:
    def test_name_is_derived_from_the_provenance_triple(self) -> None:
        name = collection_name(_provenance())
        assert name.startswith(f"face_embeddings__{TEST_MODEL_NAME}__")
        assert "scrfd_letterbox_arcface112_v1" in name

    def test_the_same_provenance_always_maps_to_the_same_collection(self) -> None:
        assert collection_name(_provenance()) == collection_name(_provenance())

    @pytest.mark.parametrize("field", ["model_name", "model_version", "preprocessing_version"])
    def test_any_provenance_change_maps_to_a_different_collection(self, field: str) -> None:
        other = _provenance(**{field: "b" * 64 if field == "model_version" else "different"})
        assert collection_name(_provenance()) != collection_name(other)


class TestStorage:
    async def test_repository_satisfies_its_protocol(
        self, repository: QdrantVectorRepository
    ) -> None:
        assert isinstance(repository, VectorRepository)

    async def test_an_embedding_round_trips(self, repository: QdrantVectorRepository) -> None:
        stored = _stored(1, uuid4())
        await repository.upsert(stored)
        found = await repository.get(stored.face_sample_uuid, stored.embedding.provenance)
        assert found is not None
        np.testing.assert_allclose(found.vector, stored.embedding.vector, atol=1e-6)
        assert found.provenance == stored.embedding.provenance

    async def test_getting_an_unknown_sample_returns_none(
        self, repository: QdrantVectorRepository
    ) -> None:
        await repository.ensure_ready(_provenance(), DIMENSION)
        assert await repository.get(uuid4(), _provenance()) is None

    async def test_getting_from_an_absent_collection_returns_none(
        self, repository: QdrantVectorRepository
    ) -> None:
        assert await repository.get(uuid4(), _provenance(model_name="never_written")) is None

    async def test_writing_the_same_sample_twice_replaces_it(
        self, repository: QdrantVectorRepository
    ) -> None:
        person = uuid4()
        first = _stored(2, person)
        await repository.upsert(first)
        replacement = StoredEmbedding(
            face_sample_uuid=first.face_sample_uuid,
            person_uuid=person,
            embedding=_embedding(3),
        )
        await repository.upsert(replacement)

        found = await repository.get(first.face_sample_uuid, first.embedding.provenance)
        assert found is not None
        np.testing.assert_allclose(found.vector, replacement.embedding.vector, atol=1e-6)

        matches = await repository.search(replacement.embedding, limit=10)
        assert [m.face_sample_uuid for m in matches] == [first.face_sample_uuid]

    async def test_a_person_may_hold_many_embeddings(
        self, repository: QdrantVectorRepository
    ) -> None:
        person = uuid4()
        samples = [_stored(seed, person) for seed in (10, 11, 12)]
        await repository.upsert_many(samples)
        matches = await repository.search(samples[0].embedding, limit=10)
        assert {m.face_sample_uuid for m in matches} == {s.face_sample_uuid for s in samples}
        assert {m.person_uuid for m in matches} == {person}

    async def test_a_mixed_provenance_write_is_refused(
        self, repository: QdrantVectorRepository
    ) -> None:
        person = uuid4()
        with pytest.raises(ValueError, match="share a provenance"):
            await repository.upsert_many(
                [_stored(20, person), _stored(21, person, _provenance(model_version="b" * 64))]
            )

    async def test_an_empty_write_is_a_no_op(self, repository: QdrantVectorRepository) -> None:
        await repository.upsert_many([])


class TestSearch:
    async def test_returns_matches_most_similar_first(
        self, repository: QdrantVectorRepository
    ) -> None:
        person = uuid4()
        target = _stored(30, person)
        await repository.upsert_many([target, _stored(31, person), _stored(32, person)])

        matches = await repository.search(target.embedding, limit=3)
        assert matches[0].face_sample_uuid == target.face_sample_uuid
        assert matches[0].score == pytest.approx(1.0, abs=1e-4)
        assert [m.score for m in matches] == sorted((m.score for m in matches), reverse=True)

    async def test_scores_are_plain_similarities_not_probabilities(
        self, repository: QdrantVectorRepository
    ) -> None:
        person = uuid4()
        stored = _stored(40, person)
        await repository.upsert(stored)
        opposite = FaceEmbedding(
            vector=(-stored.embedding.vector).astype(np.float32),
            provenance=stored.embedding.provenance,
        )
        match = (await repository.search(opposite, limit=1))[0]
        assert match.score < 0.0, "a negative score proves this is not a probability"

    async def test_limit_is_respected(self, repository: QdrantVectorRepository) -> None:
        person = uuid4()
        await repository.upsert_many([_stored(seed, person) for seed in range(50, 56)])
        assert len(await repository.search(_embedding(50), limit=2)) == 2

    async def test_an_invalid_limit_is_rejected(self, repository: QdrantVectorRepository) -> None:
        with pytest.raises(ValueError, match="limit"):
            await repository.search(_embedding(60), limit=0)

    async def test_a_person_can_be_excluded(self, repository: QdrantVectorRepository) -> None:
        mine, theirs = uuid4(), uuid4()
        await repository.upsert_many([_stored(70, mine), _stored(71, mine)])
        await repository.upsert(_stored(72, theirs))

        matches = await repository.search(_embedding(70), limit=10, exclude_person=mine)
        assert {m.person_uuid for m in matches} == {theirs}

    async def test_searching_an_absent_collection_returns_nothing(
        self, repository: QdrantVectorRepository
    ) -> None:
        assert (
            await repository.search(_embedding(80, _provenance(model_name="absent")), limit=5) == []
        )

    async def test_search_never_crosses_provenance(
        self, repository: QdrantVectorRepository
    ) -> None:
        """The isolation is structural: different triples are different indexes."""
        person = uuid4()
        old = _provenance(model_version="c" * 64)
        new = _provenance(model_version="d" * 64)
        vector = _vector(90)

        await repository.upsert(
            StoredEmbedding(
                face_sample_uuid=uuid4(),
                person_uuid=person,
                embedding=FaceEmbedding(vector=vector, provenance=old),
            )
        )
        # An identical vector under the newer provenance must not find it.
        assert await repository.search(FaceEmbedding(vector=vector, provenance=new), limit=5) == []


class TestDeletion:
    async def test_deleting_a_person_removes_only_their_vectors(
        self, repository: QdrantVectorRepository
    ) -> None:
        mine, theirs = uuid4(), uuid4()
        await repository.upsert_many([_stored(100, mine), _stored(101, mine)])
        kept = _stored(102, theirs)
        await repository.upsert(kept)

        removed = await repository.delete_person(mine, _provenance())
        assert removed == 2

        remaining = await repository.search(kept.embedding, limit=10)
        assert {m.person_uuid for m in remaining} == {theirs}

    async def test_deleting_an_unknown_person_removes_nothing(
        self, repository: QdrantVectorRepository
    ) -> None:
        await repository.ensure_ready(_provenance(), DIMENSION)
        assert await repository.delete_person(uuid4(), _provenance()) == 0

    async def test_deleting_from_an_absent_collection_is_safe(
        self, repository: QdrantVectorRepository
    ) -> None:
        assert await repository.delete_person(uuid4(), _provenance(model_name="absent")) == 0


class TestPayload:
    async def test_payload_carries_identifiers_and_provenance_only(
        self, connector: QdrantConnector, repository: QdrantVectorRepository
    ) -> None:
        """No image, no name, no free text: only uuids and model provenance."""
        stored = _stored(110, uuid4())
        await repository.upsert(stored)
        points = await connector.client.retrieve(
            collection_name=collection_name(stored.embedding.provenance),
            ids=[str(stored.face_sample_uuid)],
            with_payload=True,
        )
        assert set(points[0].payload or {}) == {
            "person_uuid",
            "face_sample_uuid",
            "model_name",
            "model_version",
            "preprocessing_version",
        }


def test_vector_match_rejects_an_out_of_range_score() -> None:
    with pytest.raises(ValueError, match=r"\[-1, 1\]"):
        VectorMatch(face_sample_uuid=uuid4(), person_uuid=uuid4(), score=1.5)
