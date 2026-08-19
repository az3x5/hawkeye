"""Retention of submitted query images."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

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
from app.connectors.postgres.audit import SqlAlchemyAuditLog, SqlAlchemyIdentificationStore
from app.domain.audit import AuditAction
from app.domain.identity import (
    DecisionOutcome,
    DecisionThresholds,
    IdentityDecision,
)
from app.domain.models import FaceSample, Person
from app.retention import purge_expired_query_images

from .conftest import INTEGRATION_DSN

pytestmark = pytest.mark.skipif(
    INTEGRATION_DSN is None, reason="set FACEID_TEST_POSTGRES_DSN to run retention tests"
)

POLICY = DecisionThresholds(accept_at=0.62, review_at=0.42, policy_version="retention-v1")
OLD_IMAGE = b"\xff\xd8\xff-an-old-query"
RECENT_IMAGE = b"\xff\xd8\xff-a-recent-query"
SHARED_IMAGE = b"\xff\xd8\xff-also-an-enrolled-sample"


@pytest_asyncio.fixture
async def connector() -> AsyncIterator[PostgresConnector]:
    assert INTEGRATION_DSN is not None
    connector = PostgresConnector(INTEGRATION_DSN)
    async with connector.engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
    yield connector
    async with connector.engine.begin() as connection:
        await connection.execute(text("TRUNCATE identifications, audit_events, persons CASCADE"))
    await connector.close()


@pytest.fixture
def objects(tmp_path: Path) -> FilesystemObjectStore:
    return FilesystemObjectStore(tmp_path / "objects")


def _decision() -> IdentityDecision:
    return IdentityDecision(
        outcome=DecisionOutcome.REVIEW,
        thresholds=POLICY,
        candidates=(),
    )


async def _record(connector: PostgresConnector, digest: str, *, age_days: int) -> None:
    identification_uuid = uuid4()
    async with connector.session() as session:
        await SqlAlchemyIdentificationStore(session).add(identification_uuid, digest, _decision())
        await session.execute(
            text("UPDATE identifications SET created_at = :when WHERE identification_uuid = :u"),
            {
                "when": datetime.now(UTC) - timedelta(days=age_days),
                "u": str(identification_uuid),
            },
        )


async def test_an_expired_query_image_is_removed(
    connector: PostgresConnector, objects: FilesystemObjectStore
) -> None:
    digest = sha256_bytes(OLD_IMAGE)
    await objects.put(digest, OLD_IMAGE)
    await _record(connector, digest, age_days=60)

    async with connector.session() as session:
        removed = await purge_expired_query_images(
            session=session,
            objects=objects,
            audit=SqlAlchemyAuditLog(session),
            retention_days=30,
        )
    assert removed == 1
    assert await objects.get(digest) is None


async def test_a_recent_query_image_is_kept(
    connector: PostgresConnector, objects: FilesystemObjectStore
) -> None:
    digest = sha256_bytes(RECENT_IMAGE)
    await objects.put(digest, RECENT_IMAGE)
    await _record(connector, digest, age_days=2)

    async with connector.session() as session:
        removed = await purge_expired_query_images(
            session=session,
            objects=objects,
            audit=SqlAlchemyAuditLog(session),
            retention_days=30,
        )
    assert removed == 0
    assert await objects.get(digest) == RECENT_IMAGE


async def test_the_identification_record_outlives_its_image(
    connector: PostgresConnector, objects: FilesystemObjectStore
) -> None:
    """The decision must stay auditable after the biometric material is gone."""
    digest = sha256_bytes(OLD_IMAGE)
    await objects.put(digest, OLD_IMAGE)
    await _record(connector, digest, age_days=60)

    async with connector.session() as session:
        await purge_expired_query_images(
            session=session,
            objects=objects,
            audit=SqlAlchemyAuditLog(session),
            retention_days=30,
        )
    async with connector.session() as session:
        rows = await session.execute(text("SELECT query_sha256 FROM identifications"))
    assert [row.query_sha256 for row in rows.all()] == [digest]


async def test_an_enrolled_sample_is_never_purged_as_a_query(
    connector: PostgresConnector, objects: FilesystemObjectStore
) -> None:
    """Enrolled faces are held under a different policy."""
    digest = sha256_bytes(SHARED_IMAGE)
    await objects.put(digest, SHARED_IMAGE)

    person = Person()
    async with connector.session() as session:
        await SqlAlchemyPersonRepository(session).add(person)
        await SqlAlchemyFaceSampleRepository(session).add(
            FaceSample(person_uuid=person.person_uuid, image_sha256=digest, source="crm")
        )
    await _record(connector, digest, age_days=90)

    async with connector.session() as session:
        removed = await purge_expired_query_images(
            session=session,
            objects=objects,
            audit=SqlAlchemyAuditLog(session),
            retention_days=30,
        )
    assert removed == 0
    assert await objects.get(digest) == SHARED_IMAGE


async def test_every_purge_is_audited(
    connector: PostgresConnector, objects: FilesystemObjectStore
) -> None:
    digest = sha256_bytes(OLD_IMAGE)
    await objects.put(digest, OLD_IMAGE)
    await _record(connector, digest, age_days=60)

    async with connector.session() as session:
        await purge_expired_query_images(
            session=session,
            objects=objects,
            audit=SqlAlchemyAuditLog(session),
            retention_days=30,
        )
    async with connector.session() as session:
        rows = await session.execute(text("SELECT action, actor_kind, details FROM audit_events"))
    events = rows.all()
    assert [e.action for e in events] == [AuditAction.QUERY_IMAGE_PURGED.value]
    assert events[0].actor_kind == "system"
    assert events[0].details["retention_days"] == 30


async def test_purging_twice_is_harmless(
    connector: PostgresConnector, objects: FilesystemObjectStore
) -> None:
    digest = sha256_bytes(OLD_IMAGE)
    await objects.put(digest, OLD_IMAGE)
    await _record(connector, digest, age_days=60)

    async with connector.session() as session:
        first = await purge_expired_query_images(
            session=session,
            objects=objects,
            audit=SqlAlchemyAuditLog(session),
            retention_days=30,
        )
    async with connector.session() as session:
        second = await purge_expired_query_images(
            session=session,
            objects=objects,
            audit=SqlAlchemyAuditLog(session),
            retention_days=30,
        )
    assert (first, second) == (1, 0)


async def test_an_empty_store_is_not_an_error(
    connector: PostgresConnector, objects: FilesystemObjectStore
) -> None:
    async with connector.session() as session:
        assert (
            await purge_expired_query_images(
                session=session,
                objects=objects,
                audit=SqlAlchemyAuditLog(session),
                retention_days=30,
            )
            == 0
        )


@pytest.mark.parametrize("bad", [0, -1])
async def test_a_nonsensical_retention_period_is_refused(
    connector: PostgresConnector, objects: FilesystemObjectStore, bad: int
) -> None:
    async with connector.session() as session:
        with pytest.raises(ValueError, match="at least 1"):
            await purge_expired_query_images(
                session=session,
                objects=objects,
                audit=SqlAlchemyAuditLog(session),
                retention_days=bad,
            )
