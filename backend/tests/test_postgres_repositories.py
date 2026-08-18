"""Integration tests for the PostgreSQL connector and repositories.

These run against a real database. Set ``FACEID_TEST_POSTGRES_DSN`` to enable
them; without it they are skipped rather than silently passing.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.connectors.postgres import (
    PostgresConnector,
    SqlAlchemyFaceSampleRepository,
    SqlAlchemyPersonRepository,
    metadata,
)
from app.domain.models import ExternalIdentifier, ExternalIdentifierKind, FaceSample, Person
from app.domain.repositories import (
    ConflictError,
    FaceSampleRepository,
    PersonRepository,
)

from .conftest import INTEGRATION_DSN

pytestmark = pytest.mark.skipif(
    INTEGRATION_DSN is None,
    reason="set FACEID_TEST_POSTGRES_DSN to run PostgreSQL integration tests",
)

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


@pytest_asyncio.fixture
async def connector() -> AsyncIterator[PostgresConnector]:
    assert INTEGRATION_DSN is not None
    connector = PostgresConnector(INTEGRATION_DSN)
    async with connector.engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
    yield connector
    async with connector.engine.begin() as connection:
        await connection.execute(text("TRUNCATE persons CASCADE"))
    await connector.close()


@pytest_asyncio.fixture
async def person(connector: PostgresConnector) -> Person:
    stored = Person()
    async with connector.session() as session:
        await SqlAlchemyPersonRepository(session).add(stored)
    return stored


async def test_connector_satisfies_the_storage_connector_contract(
    connector: PostgresConnector,
) -> None:
    assert connector.provider == "postgres"
    await connector.ping()


async def test_ping_raises_when_the_database_is_unreachable() -> None:
    dead = PostgresConnector("postgresql://nobody:nothing@127.0.0.1:1/absent")
    with pytest.raises(Exception):  # noqa: B017 - driver-specific connection error
        await dead.ping()
    await dead.close()


async def test_repositories_satisfy_their_protocols(connector: PostgresConnector) -> None:
    async with connector.session() as session:
        assert isinstance(SqlAlchemyPersonRepository(session), PersonRepository)
        assert isinstance(SqlAlchemyFaceSampleRepository(session), FaceSampleRepository)


async def test_person_round_trips_by_uuid(connector: PostgresConnector, person: Person) -> None:
    async with connector.session() as session:
        found = await SqlAlchemyPersonRepository(session).get(person.person_uuid)
    assert found is not None
    assert found.person_uuid == person.person_uuid


async def test_get_unknown_person_returns_none(connector: PostgresConnector) -> None:
    async with connector.session() as session:
        assert await SqlAlchemyPersonRepository(session).get(uuid4()) is None


async def test_external_identifier_resolves_to_its_person(
    connector: PostgresConnector, person: Person
) -> None:
    identifier = ExternalIdentifier(source="crm", kind=ExternalIdentifierKind.ID, value="42")
    async with connector.session() as session:
        repo = SqlAlchemyPersonRepository(session)
        await repo.link_external_identifier(person.person_uuid, identifier)
    async with connector.session() as session:
        found = await SqlAlchemyPersonRepository(session).find_by_external_identifier(identifier)
    assert found is not None
    assert found.person_uuid == person.person_uuid


async def test_the_same_value_from_two_sources_is_two_identifiers(
    connector: PostgresConnector, person: Person
) -> None:
    other = Person()
    crm = ExternalIdentifier(source="crm", kind=ExternalIdentifierKind.ID, value="42")
    hr = ExternalIdentifier(source="hr", kind=ExternalIdentifierKind.ID, value="42")
    async with connector.session() as session:
        repo = SqlAlchemyPersonRepository(session)
        await repo.add(other)
        await repo.link_external_identifier(person.person_uuid, crm)
        await repo.link_external_identifier(other.person_uuid, hr)

    async with connector.session() as session:
        repo = SqlAlchemyPersonRepository(session)
        from_crm = await repo.find_by_external_identifier(crm)
        from_hr = await repo.find_by_external_identifier(hr)
    assert from_crm is not None and from_hr is not None
    assert from_crm.person_uuid != from_hr.person_uuid


async def test_id_and_local_id_are_distinct_namespaces(
    connector: PostgresConnector, person: Person
) -> None:
    as_id = ExternalIdentifier(source="crm", kind=ExternalIdentifierKind.ID, value="42")
    as_local = ExternalIdentifier(source="crm", kind=ExternalIdentifierKind.LOCAL_ID, value="42")
    async with connector.session() as session:
        await SqlAlchemyPersonRepository(session).link_external_identifier(
            person.person_uuid, as_id
        )
    async with connector.session() as session:
        assert (
            await SqlAlchemyPersonRepository(session).find_by_external_identifier(as_local) is None
        )


async def test_relinking_the_same_identifier_to_the_same_person_is_idempotent(
    connector: PostgresConnector, person: Person
) -> None:
    identifier = ExternalIdentifier(source="crm", kind=ExternalIdentifierKind.ID, value="42")
    for _ in range(3):
        async with connector.session() as session:
            await SqlAlchemyPersonRepository(session).link_external_identifier(
                person.person_uuid, identifier
            )
    async with connector.session() as session:
        identifiers = await SqlAlchemyPersonRepository(session).list_external_identifiers(
            person.person_uuid
        )
    assert list(identifiers) == [identifier]


async def test_linking_one_identifier_to_two_people_conflicts(
    connector: PostgresConnector, person: Person
) -> None:
    identifier = ExternalIdentifier(source="crm", kind=ExternalIdentifierKind.ID, value="42")
    other = Person()
    async with connector.session() as session:
        repo = SqlAlchemyPersonRepository(session)
        await repo.add(other)
        await repo.link_external_identifier(person.person_uuid, identifier)
    with pytest.raises(ConflictError):
        async with connector.session() as session:
            await SqlAlchemyPersonRepository(session).link_external_identifier(
                other.person_uuid, identifier
            )


async def test_a_person_keeps_many_face_samples(
    connector: PostgresConnector, person: Person
) -> None:
    async with connector.session() as session:
        repo = SqlAlchemyFaceSampleRepository(session)
        for digest in (DIGEST_A, DIGEST_B, "c" * 64):
            await repo.add(
                FaceSample(
                    person_uuid=person.person_uuid,
                    image_sha256=digest,
                    source="upload",
                    captured_at=datetime(2026, 3, 1, tzinfo=UTC),
                )
            )
    async with connector.session() as session:
        samples = await SqlAlchemyFaceSampleRepository(session).list_for_person(person.person_uuid)
    assert len(samples) == 3
    assert len({s.image_sha256 for s in samples}) == 3


async def test_the_same_image_twice_for_one_person_conflicts(
    connector: PostgresConnector, person: Person
) -> None:
    sample = FaceSample(person_uuid=person.person_uuid, image_sha256=DIGEST_A, source="upload")
    async with connector.session() as session:
        await SqlAlchemyFaceSampleRepository(session).add(sample)
    with pytest.raises(ConflictError):
        async with connector.session() as session:
            await SqlAlchemyFaceSampleRepository(session).add(
                FaceSample(
                    person_uuid=person.person_uuid, image_sha256=DIGEST_A, source="other-upload"
                )
            )


async def test_the_same_image_may_belong_to_two_people(
    connector: PostgresConnector, person: Person
) -> None:
    other = Person()
    async with connector.session() as session:
        await SqlAlchemyPersonRepository(session).add(other)
    for owner in (person.person_uuid, other.person_uuid):
        async with connector.session() as session:
            await SqlAlchemyFaceSampleRepository(session).add(
                FaceSample(person_uuid=owner, image_sha256=DIGEST_A, source="upload")
            )
    async with connector.session() as session:
        repo = SqlAlchemyFaceSampleRepository(session)
        assert await repo.find_by_content_hash(person.person_uuid, DIGEST_A) is not None
        assert await repo.find_by_content_hash(other.person_uuid, DIGEST_A) is not None


async def test_a_sample_for_an_unknown_person_conflicts(connector: PostgresConnector) -> None:
    with pytest.raises(ConflictError):
        async with connector.session() as session:
            await SqlAlchemyFaceSampleRepository(session).add(
                FaceSample(person_uuid=uuid4(), image_sha256=DIGEST_A, source="upload")
            )


async def test_face_sample_round_trips_with_its_capture_time(
    connector: PostgresConnector, person: Person
) -> None:
    captured = datetime(2026, 3, 1, 12, 30, tzinfo=UTC)
    stored = FaceSample(
        person_uuid=person.person_uuid,
        image_sha256=DIGEST_A,
        source="cctv",
        captured_at=captured,
    )
    async with connector.session() as session:
        await SqlAlchemyFaceSampleRepository(session).add(stored)
    async with connector.session() as session:
        found = await SqlAlchemyFaceSampleRepository(session).get(stored.face_sample_uuid)
    assert found is not None
    assert found.captured_at == captured
    assert found.source == "cctv"


async def test_deleting_a_person_removes_their_samples(
    connector: PostgresConnector, person: Person
) -> None:
    async with connector.session() as session:
        await SqlAlchemyFaceSampleRepository(session).add(
            FaceSample(person_uuid=person.person_uuid, image_sha256=DIGEST_A, source="upload")
        )
    async with connector.session() as session:
        await session.execute(
            text("DELETE FROM persons WHERE person_uuid = :uuid"),
            {"uuid": str(person.person_uuid)},
        )
    async with connector.session() as session:
        samples = await SqlAlchemyFaceSampleRepository(session).list_for_person(person.person_uuid)
    assert list(samples) == []


def test_readyz_reports_postgres_when_the_app_starts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The full application registers its storage connectors as readiness probes."""
    from fastapi.testclient import TestClient

    from app.api.v1.health import ReadinessResponse
    from app.core.config import Settings
    from app.core.readiness import clear_probes
    from app.main import create_app

    assert INTEGRATION_DSN is not None
    monkeypatch.setenv("FACEID_ENVIRONMENT", "test")
    monkeypatch.setenv("FACEID_POSTGRES_DSN", INTEGRATION_DSN)
    monkeypatch.setenv("FACEID_REDIS_DSN", "redis://localhost:6379/0")
    monkeypatch.setenv("FACEID_QDRANT_URL", "http://qdrant:6333")
    monkeypatch.setenv("FACEID_OBJECT_STORE_ROOT", str(tmp_path / "objects"))
    monkeypatch.setenv("FACEID_DECISION_ACCEPT_THRESHOLD", "0.62")
    monkeypatch.setenv("FACEID_DECISION_REVIEW_THRESHOLD", "0.42")
    monkeypatch.setenv("FACEID_DECISION_POLICY_VERSION", "readiness-test-v1")

    clear_probes()
    try:
        with TestClient(create_app(Settings())) as client:  # type: ignore[call-arg]
            response = client.get("/api/v1/readyz")
    finally:
        clear_probes()

    body = ReadinessResponse.model_validate(response.json())
    probes = {c.name: c.healthy for c in body.checks}
    assert probes["postgres"] is True, f"postgres probe reported {body.checks}"
    # Qdrant is registered too; whether it is reachable depends on the
    # environment, so only its presence is asserted here.
    assert "qdrant" in probes
