"""Contract tests for the identification and review endpoints."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx2
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.dependencies import get_identification_service, get_identification_store
from app.api.v1.identifications import router as identification_router
from app.core.errors import ErrorResponse, install_error_handlers
from app.domain.audit import AuditEvent
from app.domain.auth import Scope
from app.domain.detection import BoundingBox
from app.domain.identity import (
    Candidate,
    CaptureAssurance,
    DecisionOutcome,
    DecisionThresholds,
    IdentityDecision,
    ReviewOutcome,
    StoredIdentification,
)
from app.domain.recognition import EmbeddingProvenance, FaceEmbedding
from app.services.identification import (
    IdentificationError,
    IdentificationResult,
    LiveFaceEmbedding,
    LiveFrameEmbeddings,
)

from .conftest import authenticate

POLICY = DecisionThresholds(accept_at=0.62, review_at=0.42, policy_version="api-test-v1")
JPEG = ("query.jpg", b"\xff\xd8\xff-query-bytes", "image/jpeg")


def _candidates(*scores: float) -> tuple[Candidate, ...]:
    return tuple(
        Candidate(person_uuid=uuid4(), face_sample_uuid=uuid4(), score=score, sample_count=2)
        for score in scores
    )


class FakeStore:
    def __init__(self) -> None:
        self.records: dict[UUID, StoredIdentification] = {}

    async def add(self, identification_uuid: UUID, query_sha256: str, decision: object) -> None:
        self.records[identification_uuid] = StoredIdentification(
            identification_uuid=identification_uuid,
            query_sha256=query_sha256,
            decision=decision,  # type: ignore[arg-type]
            created_at=datetime.now(UTC),
        )

    async def get(self, identification_uuid: UUID) -> StoredIdentification | None:
        return self.records.get(identification_uuid)

    async def record_review(
        self,
        identification_uuid: UUID,
        *,
        outcome: ReviewOutcome,
        reviewer: str,
        note: str | None,
        reviewed_at: datetime,
    ) -> None:
        existing = self.records[identification_uuid]
        object.__setattr__(existing, "review_outcome", outcome)
        object.__setattr__(existing, "reviewed_by", reviewer)
        object.__setattr__(existing, "reviewed_at", reviewed_at)
        object.__setattr__(existing, "review_note", note)


class FakeService:
    """Stands in for the real service; decision content is set per test."""

    def __init__(self, store: FakeStore) -> None:
        self.store = store
        self.decision = IdentityDecision(
            outcome=DecisionOutcome.ACCEPT, thresholds=POLICY, candidates=_candidates(0.9)
        )
        self.embed_error: str | None = None
        self.live_face_count = 0
        self.assurance: CaptureAssurance | None = None
        self.audit: list[AuditEvent] = []

    async def embed_query(self, image_bytes: bytes) -> FaceEmbedding:
        if self.embed_error:
            raise IdentificationError(self.embed_error)
        return FaceEmbedding(
            vector=(np.ones(512, dtype=np.float32) / np.sqrt(512)).astype(np.float32),
            provenance=EmbeddingProvenance(
                model_name="fake", model_version="v" * 8, preprocessing_version="p1"
            ),
        )

    async def identify(
        self,
        embedding: FaceEmbedding,
        *,
        query_bytes: bytes,
        actor: object = None,
        assurance: CaptureAssurance = CaptureAssurance.UNSUPERVISED,
    ) -> IdentificationResult:
        identification_uuid = uuid4()
        # The fake carries the assurance through rather than dropping it, so a
        # handler that forgot to pass it on would fail here rather than pass.
        self.assurance = assurance
        decision = replace(self.decision, assurance=assurance)
        await self.store.add(identification_uuid, "0" * 64, decision)
        return IdentificationResult(identification_uuid, decision)

    async def embed_live_frame(self, image_bytes: bytes) -> LiveFrameEmbeddings:
        embedding = await self.embed_query(image_bytes)
        return LiveFrameEmbeddings(
            width=960,
            height=540,
            faces=tuple(
                LiveFaceEmbedding(
                    box=BoundingBox(
                        x1=10.0 + index * 100,
                        y1=20.0,
                        x2=90.0 + index * 100,
                        y2=140.0,
                    ),
                    detection_score=0.95 - index * 0.05,
                    embedding=embedding,
                )
                for index in range(self.live_face_count)
            ),
        )

    async def review(
        self,
        identification_uuid: UUID,
        *,
        outcome: ReviewOutcome,
        reviewer: object,
        note: str | None = None,
    ) -> StoredIdentification:
        stored = self.store.records.get(identification_uuid)
        if stored is None:
            raise IdentificationError(f"no identification {identification_uuid}")
        if stored.decision.outcome is not DecisionOutcome.REVIEW:
            raise IdentificationError(
                f"identification {identification_uuid} was decided "
                f"'{stored.decision.outcome.value}' and was never sent for review"
            )
        if stored.review_outcome is not None:
            raise IdentificationError("already reviewed")
        await self.store.record_review(
            identification_uuid,
            outcome=outcome,
            reviewer=reviewer.identifier,  # type: ignore[attr-defined]
            note=note,
            reviewed_at=datetime.now(UTC),
        )
        updated = await self.store.get(identification_uuid)
        assert updated is not None
        return updated


@pytest.fixture
def store() -> FakeStore:
    return FakeStore()


@pytest.fixture
def service(store: FakeStore) -> FakeService:
    return FakeService(store)


@pytest.fixture
def client(service: FakeService, store: FakeStore) -> Iterator[TestClient]:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(identification_router, prefix="/api/v1")
    app.dependency_overrides[get_identification_service] = lambda: service
    app.dependency_overrides[get_identification_store] = lambda: store
    authenticate(app, Scope.IDENTIFY, Scope.REVIEW, subject="alice")
    with TestClient(app) as test_client:
        yield test_client


def _identify(client: TestClient, **data: str) -> httpx2.Response:
    return client.post("/api/v1/identifications", data=data, files={"image": JPEG})


class TestCaptureAssurance:
    """The attestation must reach the service, not stop at the schema."""

    def test_an_unattested_upload_is_treated_as_unsupervised(
        self, client: TestClient, service: FakeService
    ) -> None:
        body = _identify(client).json()
        assert service.assurance is CaptureAssurance.UNSUPERVISED
        assert body["capture_assurance"] == "unsupervised"

    def test_an_operator_can_attest_to_supervising_the_capture(
        self, client: TestClient, service: FakeService
    ) -> None:
        body = _identify(client, capture_assurance="supervised").json()
        assert service.assurance is CaptureAssurance.SUPERVISED
        assert body["capture_assurance"] == "supervised"

    def test_an_unknown_assurance_fails_validation(self, client: TestClient) -> None:
        assert _identify(client, capture_assurance="trust-me").status_code == 422

    def test_the_live_frame_endpoint_also_carries_the_attestation(
        self, client: TestClient, service: FakeService
    ) -> None:
        service.live_face_count = 1
        response = client.post(
            "/api/v1/live/frames/analyze",
            data={"capture_assurance": "supervised"},
            files={"image": JPEG},
        )
        assert response.status_code == 201
        assert service.assurance is CaptureAssurance.SUPERVISED

    def test_the_capped_flag_is_carried_to_the_caller(
        self, client: TestClient, service: FakeService
    ) -> None:
        """A reviewer must see that provenance, not the score, sent this to them."""
        service.decision = IdentityDecision(
            outcome=DecisionOutcome.REVIEW,
            thresholds=POLICY,
            candidates=_candidates(POLICY.accept_at + 0.1),
        )
        body = _identify(client).json()
        assert body["outcome"] == "review"
        assert body["capped_by_assurance"] is True


class TestIdentify:
    def test_returns_a_decision_with_its_policy(self, client: TestClient) -> None:
        response = _identify(client)
        assert response.status_code == 201
        body = response.json()
        assert body["outcome"] == "accept"
        assert body["thresholds"] == {
            "accept_at": 0.62,
            "review_at": 0.42,
            "policy_version": "api-test-v1",
        }

    def test_candidates_carry_scores_and_evidence(self, client: TestClient) -> None:
        body = _identify(client).json()
        candidate = body["candidates"][0]
        assert candidate["score"] == pytest.approx(0.9)
        assert candidate["sample_count"] == 2
        assert candidate["person_uuid"] and candidate["face_sample_uuid"]

    def test_a_review_outcome_is_reported(self, client: TestClient, service: FakeService) -> None:
        service.decision = IdentityDecision(
            outcome=DecisionOutcome.REVIEW, thresholds=POLICY, candidates=_candidates(0.5)
        )
        assert _identify(client).json()["outcome"] == "review"

    def test_a_rejection_reports_no_candidates(
        self, client: TestClient, service: FakeService
    ) -> None:
        service.decision = IdentityDecision(
            outcome=DecisionOutcome.REJECT, thresholds=POLICY, candidates=()
        )
        body = _identify(client).json()
        assert body["outcome"] == "reject"
        assert body["candidates"] == []
        assert body["margin"] is None

    def test_margin_is_reported_when_there_is_a_runner_up(
        self, client: TestClient, service: FakeService
    ) -> None:
        service.decision = IdentityDecision(
            outcome=DecisionOutcome.ACCEPT, thresholds=POLICY, candidates=_candidates(0.9, 0.8)
        )
        assert _identify(client).json()["margin"] == pytest.approx(0.1)

    def test_no_probability_is_ever_returned(self, client: TestClient) -> None:
        body = _identify(client).json()
        assert "probability" not in str(body).lower()
        assert "confidence" not in str(body).lower()

    def test_a_faceless_query_is_a_structured_error(
        self, client: TestClient, service: FakeService
    ) -> None:
        service.embed_error = "no face was detected in the query image"
        response = _identify(client)
        assert response.status_code == 422
        body = ErrorResponse.model_validate(response.json())
        assert body.error.code == "identification_failed"
        assert "no face" in body.error.message

    def test_a_multi_face_query_is_refused(self, client: TestClient, service: FakeService) -> None:
        service.embed_error = "2 faces were detected; identification requires exactly one"
        response = _identify(client)
        assert response.status_code == 422
        assert "exactly one" in ErrorResponse.model_validate(response.json()).error.message

    def test_an_unsupported_image_type_is_rejected(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/identifications", files={"image": ("q.txt", b"hello", "text/plain")}
        )
        assert response.status_code == 422
        assert ErrorResponse.model_validate(response.json()).error.field == "image"

    def test_a_missing_image_fails_validation(self, client: TestClient) -> None:
        response = client.post("/api/v1/identifications", data={})
        assert response.status_code == 422


class TestLiveFrameAnalysis:
    def test_empty_frame_is_a_successful_observation(
        self, client: TestClient, service: FakeService
    ) -> None:
        service.live_face_count = 0

        response = client.post("/api/v1/live/frames/analyze", files={"image": JPEG})

        assert response.status_code == 201
        assert response.json()["frame_width"] == 960
        assert response.json()["frame_height"] == 540
        assert response.json()["faces"] == []

    def test_returns_each_face_with_geometry_and_identity(
        self, client: TestClient, service: FakeService, store: FakeStore
    ) -> None:
        service.live_face_count = 2

        response = client.post("/api/v1/live/frames/analyze", files={"image": JPEG})

        assert response.status_code == 201
        faces = response.json()["faces"]
        assert len(faces) == 2
        assert faces[0]["detection_index"] == 0
        assert faces[0]["box"] == {"x1": 10.0, "y1": 20.0, "x2": 90.0, "y2": 140.0}
        assert faces[0]["detection_score"] == pytest.approx(0.95)
        assert faces[0]["identification"]["outcome"] == "accept"
        assert faces[1]["box"]["x1"] == pytest.approx(110.0)
        assert len(store.records) == 2

    def test_invalid_frame_uses_the_structured_error(
        self, client: TestClient, service: FakeService
    ) -> None:
        service.embed_error = "the live frame could not be decoded"

        response = client.post("/api/v1/live/frames/analyze", files={"image": JPEG})

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "identification_failed"


class TestReadIdentification:
    def test_reads_back_a_recorded_decision(self, client: TestClient) -> None:
        created = _identify(client).json()["identification_uuid"]
        response = client.get(f"/api/v1/identifications/{created}")
        assert response.status_code == 200
        assert response.json()["identification_uuid"] == created

    def test_unknown_identification_is_a_structured_404(self, client: TestClient) -> None:
        response = client.get(f"/api/v1/identifications/{uuid4()}")
        assert response.status_code == 404
        assert (
            ErrorResponse.model_validate(response.json()).error.code == "identification_not_found"
        )


class TestReview:
    @pytest.fixture
    def review_case(self, client: TestClient, service: FakeService) -> str:
        service.decision = IdentityDecision(
            outcome=DecisionOutcome.REVIEW, thresholds=POLICY, candidates=_candidates(0.5)
        )
        return str(_identify(client).json()["identification_uuid"])

    def test_a_reviewer_can_confirm(self, client: TestClient, review_case: str) -> None:
        response = client.post(
            f"/api/v1/identifications/{review_case}/review",
            json={"outcome": "confirmed", "note": "clear match"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["review_outcome"] == "confirmed"
        assert body["reviewed_by"] == "alice"
        assert body["review_note"] == "clear match"

    def test_a_reviewer_can_reject(self, client: TestClient, review_case: str) -> None:
        response = client.post(
            f"/api/v1/identifications/{review_case}/review",
            json={"outcome": "rejected"},
        )
        assert response.json()["review_outcome"] == "rejected"

    def test_reviewing_twice_conflicts(self, client: TestClient, review_case: str) -> None:
        payload = {"outcome": "confirmed"}
        client.post(f"/api/v1/identifications/{review_case}/review", json=payload)
        response = client.post(f"/api/v1/identifications/{review_case}/review", json=payload)
        assert response.status_code == 409
        assert ErrorResponse.model_validate(response.json()).error.code == "review_not_permitted"

    def test_an_automatic_accept_cannot_be_rubber_stamped(self, client: TestClient) -> None:
        """Only proposals that asked for review may be reviewed."""
        accepted = _identify(client).json()["identification_uuid"]
        response = client.post(
            f"/api/v1/identifications/{accepted}/review",
            json={"outcome": "confirmed"},
        )
        assert response.status_code == 409
        assert (
            "never sent for review" in ErrorResponse.model_validate(response.json()).error.message
        )

    def test_the_response_reflects_the_review_just_recorded(
        self, client: TestClient, review_case: str
    ) -> None:
        """The write is not committed when the endpoint answers.

        Reading it back through a second database session returned nulls, so
        the service hands the updated record straight back instead.
        """
        response = client.post(
            f"/api/v1/identifications/{review_case}/review",
            json={"outcome": "confirmed", "note": "same person"},
        )
        body = response.json()
        assert body["review_outcome"] == "confirmed"
        assert body["reviewed_by"] == "alice"
        assert body["reviewed_at"] is not None
        assert body["review_note"] == "same person"

    def test_reviewing_an_unknown_identification_is_404(self, client: TestClient) -> None:
        response = client.post(
            f"/api/v1/identifications/{uuid4()}/review",
            json={"outcome": "confirmed"},
        )
        assert response.status_code == 404

    def test_the_reviewer_is_the_authenticated_principal(
        self, client: TestClient, review_case: str
    ) -> None:
        """Not a self-declared name: the log must record people, not claims."""
        response = client.post(
            f"/api/v1/identifications/{review_case}/review", json={"outcome": "confirmed"}
        )
        assert response.status_code == 200
        assert response.json()["reviewed_by"] == "alice"

    def test_a_reviewer_named_in_the_body_is_ignored(
        self, client: TestClient, review_case: str
    ) -> None:
        response = client.post(
            f"/api/v1/identifications/{review_case}/review",
            json={"outcome": "confirmed", "reviewer": "mallory"},
        )
        assert response.status_code == 200
        assert response.json()["reviewed_by"] == "alice"

    def test_an_unknown_outcome_fails_validation(
        self, client: TestClient, review_case: str
    ) -> None:
        response = client.post(
            f"/api/v1/identifications/{review_case}/review",
            json={"outcome": "maybe"},
        )
        assert response.status_code == 422


class TestSchemas:
    def test_openapi_documents_every_endpoint(self, client: TestClient) -> None:
        schema = client.get("/openapi.json").json()
        post = schema["paths"]["/api/v1/identifications"]["post"]
        assert post["responses"]["201"]["content"]["application/json"]["schema"]
        review = schema["paths"]["/api/v1/identifications/{identification_uuid}/review"]["post"]
        assert {"200", "404", "409", "422"} <= set(review["responses"])

    def test_the_score_field_is_documented_as_not_a_probability(self, client: TestClient) -> None:
        schema = client.get("/openapi.json").json()
        description = schema["components"]["schemas"]["CandidateResponse"]["properties"]["score"][
            "description"
        ]
        assert "not a probability" in description
