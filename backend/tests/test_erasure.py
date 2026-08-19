"""Erasure of a person and their biometric material.

Integration tests against real Postgres and Qdrant: erasure spans three stores,
and the whole point is that none of them is missed.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import numpy as np
import numpy.typing as npt
import pytest
import pytest_asyncio
from sqlalchemy import text

from app.connectors.filesystem import FilesystemObjectStore
from app.connectors.filesystem.object_store import sha256_bytes
from app.connectors.postgres import (
    PostgresConnector,
    SqlAlchemyFaceSampleRepository,
    SqlAlchemyPersonRepository,
    metadata,
)
from app.connectors.postgres.audit import SqlAlchemyAuditLog
from app.connectors.qdrant import QdrantConnector, QdrantVectorRepository
from app.domain.audit import SYSTEM_ACTOR, Actor, AuditAction
from app.domain.models import FaceSample, Person
from app.domain.recognition import EmbeddingProvenance, FaceEmbedding
from app.domain.vectors import StoredEmbedding
from app.services.erasure import PersonEraser, PersonNotFoundError, reconcile_orphaned_vectors

from .conftest import INTEGRATION_DSN

QDRANT_URL = os.environ.get("FACEID_TEST_QDRANT_URL")

pytestmark = pytest.mark.skipif(
    not (INTEGRATION_DSN and QDRANT_URL),
    reason="erasure needs FACEID_TEST_POSTGRES_DSN and FACEID_TEST_QDRANT_URL",
)

#: Its own model name, so this suite never touches another suite's collections.
ERASURE_MODEL = "test_only_erasure_model"
DIMENSION = 512
IMAGE = b"\xff\xd8\xff-a-face"
OTHER_IMAGE = b"\xff\xd8\xff-another-face"


def _provenance(version: str = "a" * 64) -> EmbeddingProvenance:
    return EmbeddingProvenance(
        model_name=ERASURE_MODEL,
        model_version=version,
        preprocessing_version="p1",
    )


def _vector(seed: int) -> npt.NDArray[np.float32]:
    rng = np.random.default_rng(seed)
    raw = rng.normal(size=DIMENSION).astype(np.float32)
    return (raw / np.linalg.norm(raw)).astype(np.float32)


@pytest_asyncio.fixture
async def postgres() -> AsyncIterator[PostgresConnector]:
    assert INTEGRATION_DSN is not None
    connector = PostgresConnector(INTEGRATION_DSN)
    async with connector.engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
    yield connector
    async with connector.engine.begin() as connection:
        await connection.execute(text("TRUNCATE persons, audit_events CASCADE"))
    await connector.close()


@pytest_asyncio.fixture
async def qdrant() -> AsyncIterator[QdrantConnector]:
    assert QDRANT_URL is not None
    connector = QdrantConnector(QDRANT_URL)
    yield connector
    described = await connector.client.get_collections()
    for description in described.collections:
        if description.name.startswith(f"face_embeddings__{ERASURE_MODEL}"):
            await connector.client.delete_collection(description.name)
    await connector.close()


@pytest.fixture
def objects(tmp_path: Path) -> FilesystemObjectStore:
    return FilesystemObjectStore(tmp_path / "objects")


async def _enrol(
    postgres: PostgresConnector,
    qdrant: QdrantConnector,
    objects: FilesystemObjectStore,
    image: bytes,
    *,
    provenance: EmbeddingProvenance | None = None,
    seed: int = 1,
) -> tuple[Person, FaceSample]:
    """Put a person into all three stores, as the real pipeline would."""
    person = Person()
    digest = sha256_bytes(image)
    sample = FaceSample(person_uuid=person.person_uuid, image_sha256=digest, source="test")

    async with postgres.session() as session:
        await SqlAlchemyPersonRepository(session).add(person)
        await SqlAlchemyFaceSampleRepository(session).add(sample)
    await objects.put(digest, image)
    await QdrantVectorRepository(qdrant).upsert(
        StoredEmbedding(
            face_sample_uuid=sample.face_sample_uuid,
            person_uuid=person.person_uuid,
            embedding=FaceEmbedding(vector=_vector(seed), provenance=provenance or _provenance()),
        )
    )
    return person, sample


def _eraser(
    postgres_session: object, qdrant: QdrantConnector, objects: FilesystemObjectStore
) -> PersonEraser:
    return PersonEraser(
        session=postgres_session,  # type: ignore[arg-type]
        vectors=QdrantVectorRepository(qdrant),
        objects=objects,
        audit=SqlAlchemyAuditLog(postgres_session),  # type: ignore[arg-type]
    )


class TestErasure:
    async def test_the_person_is_gone_from_the_metadata_store(
        self,
        postgres: PostgresConnector,
        qdrant: QdrantConnector,
        objects: FilesystemObjectStore,
    ) -> None:
        person, _ = await _enrol(postgres, qdrant, objects, IMAGE)
        async with postgres.session() as session:
            await _eraser(session, qdrant, objects).erase(
                person.person_uuid, actor=Actor("admin"), reason="test"
            )
        async with postgres.session() as session:
            assert await SqlAlchemyPersonRepository(session).get(person.person_uuid) is None

    async def test_their_samples_go_with_them(
        self,
        postgres: PostgresConnector,
        qdrant: QdrantConnector,
        objects: FilesystemObjectStore,
    ) -> None:
        person, sample = await _enrol(postgres, qdrant, objects, IMAGE)
        async with postgres.session() as session:
            await _eraser(session, qdrant, objects).erase(
                person.person_uuid, actor=Actor("admin"), reason=None
            )
        async with postgres.session() as session:
            assert (
                await SqlAlchemyFaceSampleRepository(session).get(sample.face_sample_uuid) is None
            )

    async def test_their_face_is_no_longer_searchable(
        self,
        postgres: PostgresConnector,
        qdrant: QdrantConnector,
        objects: FilesystemObjectStore,
    ) -> None:
        """The whole point: erasure must reach the vector store."""
        person, sample = await _enrol(postgres, qdrant, objects, IMAGE)
        repository = QdrantVectorRepository(qdrant)
        assert await repository.get(sample.face_sample_uuid, _provenance()) is not None

        async with postgres.session() as session:
            report = await _eraser(session, qdrant, objects).erase(
                person.person_uuid, actor=Actor("admin"), reason=None
            )
        assert report.vectors_removed == 1
        assert await repository.get(sample.face_sample_uuid, _provenance()) is None

    async def test_vectors_from_every_model_version_are_removed(
        self,
        postgres: PostgresConnector,
        qdrant: QdrantConnector,
        objects: FilesystemObjectStore,
    ) -> None:
        """An older model's embeddings live in their own collection."""
        person, sample = await _enrol(postgres, qdrant, objects, IMAGE)
        older = _provenance("b" * 64)
        await QdrantVectorRepository(qdrant).upsert(
            StoredEmbedding(
                face_sample_uuid=uuid4(),
                person_uuid=person.person_uuid,
                embedding=FaceEmbedding(vector=_vector(2), provenance=older),
            )
        )

        async with postgres.session() as session:
            report = await _eraser(session, qdrant, objects).erase(
                person.person_uuid, actor=Actor("admin"), reason=None
            )
        assert report.vectors_removed == 2

    async def test_their_stored_image_is_deleted(
        self,
        postgres: PostgresConnector,
        qdrant: QdrantConnector,
        objects: FilesystemObjectStore,
    ) -> None:
        person, _ = await _enrol(postgres, qdrant, objects, IMAGE)
        async with postgres.session() as session:
            report = await _eraser(session, qdrant, objects).erase(
                person.person_uuid, actor=Actor("admin"), reason=None
            )
        assert report.images_removed == 1
        assert await objects.get(sha256_bytes(IMAGE)) is None

    async def test_an_image_shared_with_another_person_is_kept(
        self,
        postgres: PostgresConnector,
        qdrant: QdrantConnector,
        objects: FilesystemObjectStore,
    ) -> None:
        """Erasing one person must not destroy another's sample."""
        first, _ = await _enrol(postgres, qdrant, objects, IMAGE, seed=1)
        second, _ = await _enrol(postgres, qdrant, objects, IMAGE, seed=2)

        async with postgres.session() as session:
            report = await _eraser(session, qdrant, objects).erase(
                first.person_uuid, actor=Actor("admin"), reason=None
            )
        assert report.images_removed == 0
        assert await objects.get(sha256_bytes(IMAGE)) == IMAGE
        async with postgres.session() as session:
            assert await SqlAlchemyPersonRepository(session).get(second.person_uuid) is not None

    async def test_another_persons_vectors_are_untouched(
        self,
        postgres: PostgresConnector,
        qdrant: QdrantConnector,
        objects: FilesystemObjectStore,
    ) -> None:
        first, _ = await _enrol(postgres, qdrant, objects, IMAGE, seed=1)
        second, kept = await _enrol(postgres, qdrant, objects, OTHER_IMAGE, seed=2)

        async with postgres.session() as session:
            await _eraser(session, qdrant, objects).erase(
                first.person_uuid, actor=Actor("admin"), reason=None
            )
        assert (
            await QdrantVectorRepository(qdrant).get(kept.face_sample_uuid, _provenance())
            is not None
        )

    async def test_erasure_is_audited_and_the_record_survives(
        self,
        postgres: PostgresConnector,
        qdrant: QdrantConnector,
        objects: FilesystemObjectStore,
    ) -> None:
        """A deletion that leaves no trace cannot be shown to have happened."""
        person, _ = await _enrol(postgres, qdrant, objects, IMAGE)
        async with postgres.session() as session:
            await _eraser(session, qdrant, objects).erase(
                person.person_uuid, actor=Actor("admin@example.com"), reason="subject request"
            )
        async with postgres.session() as session:
            events = await SqlAlchemyAuditLog(session).for_person(person.person_uuid)
        assert len(events) == 1
        assert events[0].action is AuditAction.PERSON_ERASED
        assert events[0].actor.identifier == "admin@example.com"
        assert events[0].details["reason"] == "subject request"
        assert events[0].details["vectors_removed"] == 1

    async def test_erasing_an_unknown_person_is_reported(
        self,
        postgres: PostgresConnector,
        qdrant: QdrantConnector,
        objects: FilesystemObjectStore,
    ) -> None:
        async with postgres.session() as session:
            with pytest.raises(PersonNotFoundError):
                await _eraser(session, qdrant, objects).erase(
                    uuid4(), actor=Actor("admin"), reason=None
                )

    async def test_erasing_twice_is_reported_not_silently_accepted(
        self,
        postgres: PostgresConnector,
        qdrant: QdrantConnector,
        objects: FilesystemObjectStore,
    ) -> None:
        person, _ = await _enrol(postgres, qdrant, objects, IMAGE)
        async with postgres.session() as session:
            await _eraser(session, qdrant, objects).erase(
                person.person_uuid, actor=Actor("admin"), reason=None
            )
        async with postgres.session() as session:
            with pytest.raises(PersonNotFoundError):
                await _eraser(session, qdrant, objects).erase(
                    person.person_uuid, actor=Actor("admin"), reason=None
                )


class TestReconciliation:
    async def test_vectors_without_a_person_are_removed(
        self,
        postgres: PostgresConnector,
        qdrant: QdrantConnector,
        objects: FilesystemObjectStore,
    ) -> None:
        """A face with no person attached is the worst kind of leftover."""
        person, sample = await _enrol(postgres, qdrant, objects, IMAGE)
        # Delete the person the way a pre-erasure system would have: metadata only.
        async with postgres.session() as session:
            await session.execute(
                text("DELETE FROM persons WHERE person_uuid = :u"),
                {"u": str(person.person_uuid)},
            )
        repository = QdrantVectorRepository(qdrant)
        assert await repository.get(sample.face_sample_uuid, _provenance()) is not None

        async with postgres.session() as session:
            removed = await reconcile_orphaned_vectors(
                session=session, vectors=repository, audit=SqlAlchemyAuditLog(session)
            )
        # Reconciliation scans every collection, so the exact count depends on
        # whatever else the environment is holding. What matters is that this
        # orphan is gone and that it was counted.
        assert removed >= 1
        assert await repository.get(sample.face_sample_uuid, _provenance()) is None

    async def test_vectors_with_a_person_are_kept(
        self,
        postgres: PostgresConnector,
        qdrant: QdrantConnector,
        objects: FilesystemObjectStore,
    ) -> None:
        _, sample = await _enrol(postgres, qdrant, objects, IMAGE)
        async with postgres.session() as session:
            before = await reconcile_orphaned_vectors(
                session=session,
                vectors=QdrantVectorRepository(qdrant),
                audit=SqlAlchemyAuditLog(session),
            )
        _ = before
        assert (
            await QdrantVectorRepository(qdrant).get(sample.face_sample_uuid, _provenance())
            is not None
        )

    async def test_a_dry_run_reports_without_deleting(
        self,
        postgres: PostgresConnector,
        qdrant: QdrantConnector,
        objects: FilesystemObjectStore,
    ) -> None:
        person, sample = await _enrol(postgres, qdrant, objects, IMAGE)
        async with postgres.session() as session:
            await session.execute(
                text("DELETE FROM persons WHERE person_uuid = :u"),
                {"u": str(person.person_uuid)},
            )
        async with postgres.session() as session:
            found = await reconcile_orphaned_vectors(
                session=session,
                vectors=QdrantVectorRepository(qdrant),
                audit=SqlAlchemyAuditLog(session),
                dry_run=True,
            )
        assert found >= 1
        assert (
            await QdrantVectorRepository(qdrant).get(sample.face_sample_uuid, _provenance())
            is not None
        )

    async def test_reconciliation_is_audited(
        self,
        postgres: PostgresConnector,
        qdrant: QdrantConnector,
        objects: FilesystemObjectStore,
    ) -> None:
        person, _ = await _enrol(postgres, qdrant, objects, IMAGE)
        async with postgres.session() as session:
            await session.execute(
                text("DELETE FROM persons WHERE person_uuid = :u"),
                {"u": str(person.person_uuid)},
            )
        async with postgres.session() as session:
            await reconcile_orphaned_vectors(
                session=session,
                vectors=QdrantVectorRepository(qdrant),
                audit=SqlAlchemyAuditLog(session),
            )
        async with postgres.session() as session:
            events = await SqlAlchemyAuditLog(session).for_person(person.person_uuid)
        assert [e.action for e in events] == [AuditAction.ORPHANED_VECTORS_PURGED]
        assert events[0].actor == SYSTEM_ACTOR

    async def test_an_empty_store_is_not_an_error(
        self, postgres: PostgresConnector, qdrant: QdrantConnector
    ) -> None:
        async with postgres.session() as session:
            assert (
                await reconcile_orphaned_vectors(
                    session=session,
                    vectors=QdrantVectorRepository(qdrant),
                    audit=SqlAlchemyAuditLog(session),
                )
                == 0
            )
