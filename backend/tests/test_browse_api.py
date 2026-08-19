"""Read endpoints for browsing people, history, audit and statistics."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.v1.browse import router as browse_router
from app.api.v1.dependencies import get_read_queries
from app.api.v1.security import get_principal
from app.connectors.postgres import (
    PostgresConnector,
    SqlAlchemyFaceSampleRepository,
    SqlAlchemyPersonRepository,
    metadata,
)
from app.connectors.postgres.audit import SqlAlchemyAuditLog, SqlAlchemyIdentificationStore
from app.connectors.postgres.queries import ReadQueries
from app.core.errors import ErrorResponse, install_error_handlers
from app.domain.audit import SYSTEM_ACTOR, Actor, AuditAction, AuditEvent
from app.domain.auth import Principal, Scope
from app.domain.identity import (
    Candidate,
    DecisionOutcome,
    DecisionThresholds,
    IdentityDecision,
)
from app.domain.models import ExternalIdentifier, ExternalIdentifierKind, FaceSample, Person

from .conftest import INTEGRATION_DSN

pytestmark = pytest.mark.skipif(
    INTEGRATION_DSN is None, reason="browse tests need FACEID_TEST_POSTGRES_DSN"
)

POLICY = DecisionThresholds(accept_at=0.62, review_at=0.42, policy_version="browse-v1")


def _run[T](work: Callable[[PostgresConnector], Awaitable[T]]) -> T:
    async def run() -> T:
        assert INTEGRATION_DSN is not None
        connector = PostgresConnector(INTEGRATION_DSN)
        try:
            return await work(connector)
        finally:
            await connector.close()

    return asyncio.run(run())


@pytest.fixture(autouse=True)
def schema() -> Iterator[None]:
    async def create(connector: PostgresConnector) -> None:
        async with connector.engine.begin() as connection:
            await connection.run_sync(metadata.create_all)

    async def clean(connector: PostgresConnector) -> None:
        async with connector.engine.begin() as connection:
            await connection.execute(
                text("TRUNCATE persons, identifications, audit_events CASCADE")
            )

    # Cleaned before as well as after: these tests assert on totals, so
    # anything another test file left behind would change the answer.
    _run(create)
    _run(clean)
    yield
    _run(clean)


def _client(*scopes: Scope) -> Iterator[TestClient]:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        assert INTEGRATION_DSN is not None
        connector = PostgresConnector(INTEGRATION_DSN)
        application.state.connector = connector
        yield
        await connector.close()

    app = FastAPI(lifespan=lifespan)
    install_error_handlers(app)
    app.include_router(browse_router, prefix="/api/v1")

    async def queries() -> AsyncIterator[ReadQueries]:
        async with app.state.connector.session() as session:
            yield ReadQueries(session)

    app.dependency_overrides[get_read_queries] = queries
    app.dependency_overrides[get_principal] = lambda: Principal(
        token_uuid=uuid4(), subject="admin@example.com", kind="user", scopes=frozenset(scopes)
    )
    with TestClient(app) as client:
        yield client


@pytest.fixture
def client() -> Iterator[TestClient]:
    yield from _client(Scope.REVIEW, Scope.ADMIN)


@pytest.fixture
def reviewer() -> Iterator[TestClient]:
    yield from _client(Scope.REVIEW)


async def _seed(connector: PostgresConnector) -> dict[str, Any]:
    """Put one person, sample, identification and two audit events in place.

    The external identifier is unique per call: one identifier denotes one
    person within its source, so reusing it would conflict — correctly.
    """
    person = Person()
    handle = f"alice-{uuid4().hex[:8]}"
    sample = FaceSample(person_uuid=person.person_uuid, image_sha256="a" * 64, source="crm")
    identification = uuid4()

    async with connector.session() as session:
        people = SqlAlchemyPersonRepository(session)
        await people.add(person)
        await people.link_external_identifier(
            person.person_uuid,
            ExternalIdentifier(source="crm", kind=ExternalIdentifierKind.ID, value=handle),
        )
        await SqlAlchemyFaceSampleRepository(session).add(sample)
        await SqlAlchemyIdentificationStore(session).add(
            identification,
            "b" * 64,
            IdentityDecision(
                outcome=DecisionOutcome.ACCEPT,
                thresholds=POLICY,
                candidates=(
                    Candidate(
                        person_uuid=person.person_uuid,
                        face_sample_uuid=sample.face_sample_uuid,
                        score=0.91,
                        sample_count=1,
                    ),
                ),
            ),
        )
        await SqlAlchemyAuditLog(session).record(
            AuditEvent(
                action=AuditAction.IDENTIFICATION_PERFORMED,
                actor=SYSTEM_ACTOR,
                person_uuid=person.person_uuid,
                identification_uuid=identification,
                occurred_at=datetime.now(UTC),
            )
        )
        await SqlAlchemyAuditLog(session).record(
            AuditEvent(
                action=AuditAction.ACCOUNT_CREATED,
                actor=Actor("admin@example.com"),
                details={"email": "someone@example.com"},
            )
        )
    return {"person": person, "sample": sample, "identification": identification, "handle": handle}


class TestPersons:
    def test_lists_people_with_their_samples_and_identifiers(self, client: TestClient) -> None:
        seeded = _run(_seed)
        body = client.get("/api/v1/persons").json()
        assert body["total"] == 1
        entry = body["items"][0]
        assert entry["person_uuid"] == str(seeded["person"].person_uuid)
        assert entry["sample_count"] == 1
        assert entry["identifiers"] == [{"source": "crm", "kind": "id", "value": seeded["handle"]}]

    def test_search_matches_an_external_identifier(self, client: TestClient) -> None:
        seeded = _run(_seed)
        assert client.get(f"/api/v1/persons?search={seeded['handle']}").json()["total"] == 1
        assert client.get("/api/v1/persons?search=nobody").json()["total"] == 0

    def test_search_matches_a_person_uuid(self, client: TestClient) -> None:
        seeded = _run(_seed)
        prefix = str(seeded["person"].person_uuid)[:8]
        assert client.get(f"/api/v1/persons?search={prefix}").json()["total"] == 1

    def test_pagination_reports_the_full_total(self, client: TestClient) -> None:
        _run(_seed)
        _run(_seed)
        body = client.get("/api/v1/persons?limit=1").json()
        assert body["total"] == 2
        assert len(body["items"]) == 1

    @pytest.mark.parametrize("query", ["limit=0", "limit=500", "offset=-1"])
    def test_invalid_paging_fails_validation(self, client: TestClient, query: str) -> None:
        response = client.get(f"/api/v1/persons?{query}")
        assert response.status_code == 422
        assert ErrorResponse.model_validate(response.json()).error.code == "validation_error"

    def test_reads_one_person_with_their_samples(self, client: TestClient) -> None:
        seeded = _run(_seed)
        body = client.get(f"/api/v1/persons/{seeded['person'].person_uuid}").json()
        assert body["sample_count"] == 1
        assert body["samples"][0]["face_sample_uuid"] == str(seeded["sample"].face_sample_uuid)
        assert body["samples"][0]["processing_state"] == "pending"

    def test_an_unknown_person_is_a_structured_404(self, client: TestClient) -> None:
        response = client.get(f"/api/v1/persons/{uuid4()}")
        assert response.status_code == 404
        assert ErrorResponse.model_validate(response.json()).error.code == "person_not_found"

    def test_browsing_needs_the_review_scope(self) -> None:
        for client in _client(Scope.ENROL):
            assert client.get("/api/v1/persons").status_code == 403
            break


class TestIdentificationHistory:
    def test_lists_decided_identifications(self, client: TestClient) -> None:
        seeded = _run(_seed)
        body = client.get("/api/v1/identification-history").json()
        assert body["total"] == 1
        entry = body["items"][0]
        assert entry["identification_uuid"] == str(seeded["identification"])
        assert entry["outcome"] == "accept"
        assert entry["policy_version"] == "browse-v1"
        assert entry["candidates"][0]["score"] == pytest.approx(0.91)

    def test_history_includes_what_the_review_queue_excludes(self, client: TestClient) -> None:
        """The queue shows only unreviewed proposals; this shows everything."""
        _run(_seed)
        assert client.get("/api/v1/identification-history").json()["total"] == 1
        assert client.get("/api/v1/identification-history?outcome=review").json()["total"] == 0

    def test_filters_by_outcome_and_review_state(self, client: TestClient) -> None:
        _run(_seed)
        assert client.get("/api/v1/identification-history?outcome=accept").json()["total"] == 1
        assert client.get("/api/v1/identification-history?reviewed=false").json()["total"] == 1
        assert client.get("/api/v1/identification-history?reviewed=true").json()["total"] == 0

    def test_filters_by_person(self, client: TestClient) -> None:
        seeded = _run(_seed)
        person = seeded["person"].person_uuid
        assert (
            client.get(f"/api/v1/identification-history?person_uuid={person}").json()["total"] == 1
        )
        assert (
            client.get(f"/api/v1/identification-history?person_uuid={uuid4()}").json()["total"] == 0
        )

    def test_an_unknown_outcome_fails_validation(self, client: TestClient) -> None:
        assert client.get("/api/v1/identification-history?outcome=maybe").status_code == 422


class TestAudit:
    def test_lists_events_most_recent_first(self, client: TestClient) -> None:
        _run(_seed)
        body = client.get("/api/v1/audit-events").json()
        assert body["total"] == 2
        assert body["items"][0]["occurred_at"] >= body["items"][1]["occurred_at"]

    def test_reports_the_actions_available_to_filter_by(self, client: TestClient) -> None:
        _run(_seed)
        actions = client.get("/api/v1/audit-events").json()["actions"]
        assert "account_created" in actions
        assert "identification_performed" in actions

    def test_filters_by_action_actor_and_person(self, client: TestClient) -> None:
        seeded = _run(_seed)
        assert client.get("/api/v1/audit-events?action=account_created").json()["total"] == 1
        assert client.get("/api/v1/audit-events?actor=admin").json()["total"] == 1
        person = seeded["person"].person_uuid
        assert client.get(f"/api/v1/audit-events?person_uuid={person}").json()["total"] == 1

    def test_a_system_actor_is_distinguishable(self, client: TestClient) -> None:
        _run(_seed)
        kinds = {item["actor_kind"] for item in client.get("/api/v1/audit-events").json()["items"]}
        assert kinds == {"system", "user"}

    def test_reading_the_audit_log_needs_the_admin_scope(self, reviewer: TestClient) -> None:
        response = reviewer.get("/api/v1/audit-events")
        assert response.status_code == 403
        assert ErrorResponse.model_validate(response.json()).error.code == "not_authorised"

    def test_there_is_no_way_to_change_an_audit_event(self, client: TestClient) -> None:
        """The log is evidence; nothing may edit or remove it."""
        assert client.post("/api/v1/audit-events", json={}).status_code == 405
        assert client.delete("/api/v1/audit-events").status_code == 405


class TestStatistics:
    def test_counts_are_real(self, client: TestClient) -> None:
        _run(_seed)
        body = client.get("/api/v1/statistics").json()
        assert body["persons"] == 1
        assert body["face_samples"] == 1
        assert body["identifications"] == 1
        assert body["samples_by_state"] == {"pending": 1}
        assert body["identifications_by_outcome"] == {"accept": 1}
        assert body["audit_events"] == 2

    def test_an_empty_system_reports_zeroes_rather_than_nothing(self, client: TestClient) -> None:
        body = client.get("/api/v1/statistics").json()
        assert body["persons"] == 0
        assert body["samples_by_state"] == {}
        assert body["awaiting_review"] == 0

    def test_statistics_need_the_review_scope(self) -> None:
        for client in _client(Scope.ENROL):
            assert client.get("/api/v1/statistics").status_code == 403
            break
