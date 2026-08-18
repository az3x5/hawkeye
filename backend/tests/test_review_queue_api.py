"""Contract tests for the review queue and the image endpoints."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.dependencies import (
    get_identification_store,
    get_object_store,
    get_sample_reader,
)
from app.api.v1.enrolments import router as enrolment_router
from app.api.v1.identifications import router as identification_router
from app.connectors.filesystem import FilesystemObjectStore
from app.connectors.filesystem.object_store import sha256_bytes
from app.core.errors import ErrorResponse, install_error_handlers
from app.domain.identity import (
    Candidate,
    DecisionOutcome,
    DecisionThresholds,
    IdentityDecision,
    ReviewOutcome,
    StoredIdentification,
)
from app.domain.models import FaceSample

POLICY = DecisionThresholds(accept_at=0.62, review_at=0.42, policy_version="queue-test-v1")
QUERY_IMAGE = b"\xff\xd8\xff-the-submitted-query"
SAMPLE_IMAGE = b"\xff\xd8\xff-an-enrolled-sample"


def _stored(
    outcome: DecisionOutcome,
    *,
    created_at: datetime,
    reviewed: ReviewOutcome | None = None,
    query_sha256: str = sha256_bytes(QUERY_IMAGE),
) -> StoredIdentification:
    return StoredIdentification(
        identification_uuid=uuid4(),
        query_sha256=query_sha256,
        decision=IdentityDecision(
            outcome=outcome,
            thresholds=POLICY,
            candidates=(
                Candidate(
                    person_uuid=uuid4(),
                    face_sample_uuid=uuid4(),
                    score=0.5,
                    sample_count=1,
                ),
                Candidate(
                    person_uuid=uuid4(),
                    face_sample_uuid=uuid4(),
                    score=0.44,
                    sample_count=1,
                ),
            ),
        ),
        created_at=created_at,
        review_outcome=reviewed,
    )


class FakeStore:
    def __init__(self) -> None:
        self.records: dict[UUID, StoredIdentification] = {}

    def add_record(self, stored: StoredIdentification) -> StoredIdentification:
        self.records[stored.identification_uuid] = stored
        return stored

    async def add(self, identification_uuid: UUID, query_sha256: str, decision: object) -> None:
        raise NotImplementedError

    async def get(self, identification_uuid: UUID) -> StoredIdentification | None:
        return self.records.get(identification_uuid)

    async def record_review(self, identification_uuid: UUID, **kwargs: object) -> None:
        raise NotImplementedError

    async def awaiting_review(self, *, limit: int) -> list[StoredIdentification]:
        pending = [
            r
            for r in self.records.values()
            if r.decision.outcome is DecisionOutcome.REVIEW and r.review_outcome is None
        ]
        pending.sort(key=lambda r: r.created_at)
        return pending[:limit]


class FakeSampleReader:
    def __init__(self) -> None:
        self.samples: dict[UUID, FaceSample] = {}

    async def get(self, face_sample_uuid: UUID) -> FaceSample | None:
        return self.samples.get(face_sample_uuid)


@pytest.fixture
def store() -> FakeStore:
    return FakeStore()


@pytest.fixture
def samples() -> FakeSampleReader:
    return FakeSampleReader()


@pytest.fixture
def objects(tmp_path: Path) -> FilesystemObjectStore:
    return FilesystemObjectStore(tmp_path / "objects")


@pytest.fixture
def client(
    store: FakeStore, samples: FakeSampleReader, objects: FilesystemObjectStore
) -> Iterator[TestClient]:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(identification_router, prefix="/api/v1")
    app.include_router(enrolment_router, prefix="/api/v1")
    app.dependency_overrides[get_identification_store] = lambda: store
    app.dependency_overrides[get_sample_reader] = lambda: samples
    app.dependency_overrides[get_object_store] = lambda: objects
    with TestClient(app) as test_client:
        yield test_client


class TestReviewQueue:
    def test_lists_only_proposals_awaiting_a_human(
        self, client: TestClient, store: FakeStore
    ) -> None:
        now = datetime.now(UTC)
        pending = store.add_record(_stored(DecisionOutcome.REVIEW, created_at=now))
        store.add_record(_stored(DecisionOutcome.ACCEPT, created_at=now))
        store.add_record(_stored(DecisionOutcome.REJECT, created_at=now))
        store.add_record(
            _stored(DecisionOutcome.REVIEW, created_at=now, reviewed=ReviewOutcome.CONFIRMED)
        )

        body = client.get("/api/v1/identifications").json()
        assert body["count"] == 1
        assert body["items"][0]["identification_uuid"] == str(pending.identification_uuid)

    def test_the_backlog_is_oldest_first(self, client: TestClient, store: FakeStore) -> None:
        now = datetime.now(UTC)
        newer = store.add_record(_stored(DecisionOutcome.REVIEW, created_at=now))
        older = store.add_record(
            _stored(DecisionOutcome.REVIEW, created_at=now - timedelta(hours=3))
        )
        items = client.get("/api/v1/identifications").json()["items"]
        assert [i["identification_uuid"] for i in items] == [
            str(older.identification_uuid),
            str(newer.identification_uuid),
        ]

    def test_each_entry_carries_the_evidence_a_reviewer_needs(
        self, client: TestClient, store: FakeStore
    ) -> None:
        store.add_record(_stored(DecisionOutcome.REVIEW, created_at=datetime.now(UTC)))
        entry = client.get("/api/v1/identifications").json()["items"][0]
        assert entry["best_score"] == pytest.approx(0.5)
        assert entry["margin"] == pytest.approx(0.06)
        assert entry["candidate_count"] == 2
        assert entry["policy_version"] == "queue-test-v1"

    def test_an_empty_queue_is_not_an_error(self, client: TestClient) -> None:
        body = client.get("/api/v1/identifications").json()
        assert body == {"items": [], "count": 0}

    def test_the_limit_is_applied(self, client: TestClient, store: FakeStore) -> None:
        now = datetime.now(UTC)
        for _ in range(5):
            store.add_record(_stored(DecisionOutcome.REVIEW, created_at=now))
        assert client.get("/api/v1/identifications?limit=2").json()["count"] == 2

    @pytest.mark.parametrize("bad", ["0", "-1", "500", "many"])
    def test_an_invalid_limit_fails_validation(self, client: TestClient, bad: str) -> None:
        response = client.get(f"/api/v1/identifications?limit={bad}")
        assert response.status_code == 422
        assert ErrorResponse.model_validate(response.json()).error.code == "validation_error"


class TestQueryImage:
    def test_serves_the_image_that_was_submitted(
        self, client: TestClient, store: FakeStore, objects: FilesystemObjectStore
    ) -> None:
        import asyncio

        stored = store.add_record(_stored(DecisionOutcome.REVIEW, created_at=datetime.now(UTC)))
        asyncio.run(objects.put(sha256_bytes(QUERY_IMAGE), QUERY_IMAGE))

        response = client.get(f"/api/v1/identifications/{stored.identification_uuid}/image")
        assert response.status_code == 200
        assert response.content == QUERY_IMAGE
        assert response.headers["content-type"] == "image/jpeg"

    def test_biometric_images_are_not_cached_by_shared_caches(
        self, client: TestClient, store: FakeStore, objects: FilesystemObjectStore
    ) -> None:
        import asyncio

        stored = store.add_record(_stored(DecisionOutcome.REVIEW, created_at=datetime.now(UTC)))
        asyncio.run(objects.put(sha256_bytes(QUERY_IMAGE), QUERY_IMAGE))
        response = client.get(f"/api/v1/identifications/{stored.identification_uuid}/image")
        assert response.headers["cache-control"] == "private, no-store"

    def test_an_unknown_identification_is_a_structured_404(self, client: TestClient) -> None:
        response = client.get(f"/api/v1/identifications/{uuid4()}/image")
        assert response.status_code == 404
        assert (
            ErrorResponse.model_validate(response.json()).error.code == "identification_not_found"
        )

    def test_a_missing_object_is_reported_not_crashed(
        self, client: TestClient, store: FakeStore
    ) -> None:
        stored = store.add_record(_stored(DecisionOutcome.REVIEW, created_at=datetime.now(UTC)))
        response = client.get(f"/api/v1/identifications/{stored.identification_uuid}/image")
        assert response.status_code == 404
        assert "no longer stored" in ErrorResponse.model_validate(response.json()).error.message

    def test_images_are_not_reachable_by_content_hash_alone(
        self, client: TestClient, objects: FilesystemObjectStore
    ) -> None:
        """Possessing a hash must not be enough to retrieve someone's face."""
        import asyncio

        digest = sha256_bytes(QUERY_IMAGE)
        asyncio.run(objects.put(digest, QUERY_IMAGE))
        assert client.get(f"/api/v1/identifications/{digest}/image").status_code == 422
        assert client.get(f"/api/v1/objects/{digest}").status_code == 404


class TestSampleImage:
    def test_serves_an_enrolled_sample(
        self, client: TestClient, samples: FakeSampleReader, objects: FilesystemObjectStore
    ) -> None:
        import asyncio

        sample = FaceSample(
            person_uuid=uuid4(),
            image_sha256=sha256_bytes(SAMPLE_IMAGE),
            source="crm",
        )
        samples.samples[sample.face_sample_uuid] = sample
        asyncio.run(objects.put(sample.image_sha256, SAMPLE_IMAGE))

        response = client.get(f"/api/v1/face-samples/{sample.face_sample_uuid}/image")
        assert response.status_code == 200
        assert response.content == SAMPLE_IMAGE
        assert response.headers["cache-control"] == "private, no-store"

    def test_an_unknown_sample_is_a_structured_404(self, client: TestClient) -> None:
        response = client.get(f"/api/v1/face-samples/{uuid4()}/image")
        assert response.status_code == 404
        assert ErrorResponse.model_validate(response.json()).error.code == "face_sample_not_found"


def test_openapi_documents_the_queue_and_image_endpoints(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    assert schema["paths"]["/api/v1/identifications"]["get"]["responses"]["200"]
    image = schema["paths"]["/api/v1/identifications/{identification_uuid}/image"]["get"]
    assert "image/jpeg" in image["responses"]["200"]["content"]
