"""Contract tests for the enrolment endpoints."""

from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.dependencies import get_enrolment_service, get_sample_reader
from app.api.v1.enrolments import MAX_IMAGE_BYTES
from app.api.v1.enrolments import router as enrolment_router
from app.core.errors import ErrorResponse, install_error_handlers
from app.domain.jobs import ProcessingState
from app.services.enrolment import EnrolmentService, SampleReader

from .test_enrolment_service import (
    IMAGE,
    FakeObjects,
    FakePeople,
    FakeQueue,
    FakeSamples,
)

JPEG = ("photo.jpg", IMAGE, "image/jpeg")


@pytest.fixture
def fakes() -> tuple[FakePeople, FakeSamples, FakeObjects, FakeQueue]:
    return FakePeople(), FakeSamples(), FakeObjects(), FakeQueue()


@pytest.fixture
def client(
    settings: object, fakes: tuple[FakePeople, FakeSamples, FakeObjects, FakeQueue]
) -> Iterator[TestClient]:
    people, samples, objects, queue = fakes
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(enrolment_router, prefix="/api/v1")

    async def _service() -> EnrolmentService:
        return EnrolmentService(people=people, samples=samples, objects=objects, queue=queue)

    async def _reader() -> SampleReader:
        return SampleReader(samples)

    app.dependency_overrides[get_enrolment_service] = _service
    app.dependency_overrides[get_sample_reader] = _reader
    with TestClient(app) as test_client:
        yield test_client


def _enrol(client: TestClient, **form: str) -> httpx2.Response:
    data = {"source": "crm", "external_id": "42"}
    data.update(form)
    return client.post("/api/v1/enrolments", data=data, files={"image": JPEG})


class TestCreateEnrolment:
    def test_accepts_a_first_enrolment(self, client: TestClient) -> None:
        response = _enrol(client)
        assert response.status_code == 202
        body = response.json()
        assert body["created"] is True
        assert body["status"] == "accepted"
        assert body["sample"]["processing_state"] == ProcessingState.PENDING.value
        assert body["person_uuid"] == body["sample"]["person_uuid"]

    def test_a_repeat_returns_the_same_identifiers(self, client: TestClient) -> None:
        first = _enrol(client).json()
        second = _enrol(client).json()
        assert second["created"] is False
        assert second["status"] == "already_enrolled"
        assert second["person_uuid"] == first["person_uuid"]
        assert second["sample"]["face_sample_uuid"] == first["sample"]["face_sample_uuid"]

    def test_a_repeat_schedules_no_further_work(
        self, client: TestClient, fakes: tuple[FakePeople, FakeSamples, FakeObjects, FakeQueue]
    ) -> None:
        _enrol(client)
        _enrol(client)
        assert len(fakes[3].jobs) == 1

    def test_local_id_alone_is_accepted(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/enrolments",
            data={"source": "crm", "local_id": "L-9"},
            files={"image": JPEG},
        )
        assert response.status_code == 202

    def test_missing_identifiers_are_rejected_with_a_structured_error(
        self, client: TestClient
    ) -> None:
        response = client.post("/api/v1/enrolments", data={"source": "crm"}, files={"image": JPEG})
        assert response.status_code == 422
        body = ErrorResponse.model_validate(response.json())
        assert body.error.code == "invalid_enrolment"

    def test_missing_source_fails_validation(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/enrolments", data={"external_id": "42"}, files={"image": JPEG}
        )
        assert response.status_code == 422
        body = ErrorResponse.model_validate(response.json())
        assert body.error.code == "validation_error"
        assert "source" in {d.field for d in body.details}

    def test_a_missing_image_fails_validation(self, client: TestClient) -> None:
        response = client.post("/api/v1/enrolments", data={"source": "crm", "external_id": "42"})
        assert response.status_code == 422

    def test_an_unsupported_image_type_is_rejected(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/enrolments",
            data={"source": "crm", "external_id": "42"},
            files={"image": ("notes.txt", b"hello", "text/plain")},
        )
        assert response.status_code == 422
        body = ErrorResponse.model_validate(response.json())
        assert body.error.code == "invalid_enrolment"
        assert body.error.field == "image"

    def test_an_empty_image_is_rejected(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/enrolments",
            data={"source": "crm", "external_id": "42"},
            files={"image": ("empty.jpg", b"", "image/jpeg")},
        )
        assert response.status_code == 422
        assert ErrorResponse.model_validate(response.json()).error.field == "image"

    def test_an_oversized_image_is_rejected(self, client: TestClient) -> None:
        oversized = b"\xff\xd8\xff" + b"0" * MAX_IMAGE_BYTES
        response = client.post(
            "/api/v1/enrolments",
            data={"source": "crm", "external_id": "42"},
            files={"image": ("big.jpg", oversized, "image/jpeg")},
        )
        assert response.status_code == 422
        body = ErrorResponse.model_validate(response.json())
        assert "exceeds" in body.error.message

    def test_conflicting_identifiers_return_409(self, client: TestClient) -> None:
        _enrol(client, external_id="1")
        client.post(
            "/api/v1/enrolments",
            data={"source": "crm", "local_id": "2"},
            files={"image": ("other.jpg", b"\xff\xd8\xff-other", "image/jpeg")},
        )
        response = client.post(
            "/api/v1/enrolments",
            data={"source": "crm", "external_id": "1", "local_id": "2"},
            files={"image": ("third.jpg", b"\xff\xd8\xff-third", "image/jpeg")},
        )
        assert response.status_code == 409
        body = ErrorResponse.model_validate(response.json())
        assert body.error.code == "conflicting_identifiers"

    def test_the_response_never_echoes_image_bytes(self, client: TestClient) -> None:
        body = _enrol(client).json()
        assert IMAGE.decode("latin-1") not in str(body)
        assert body["sample"]["image_sha256"]


class TestReadFaceSample:
    def test_reads_an_enrolled_sample(self, client: TestClient) -> None:
        created = _enrol(client).json()["sample"]["face_sample_uuid"]
        response = client.get(f"/api/v1/face-samples/{created}")
        assert response.status_code == 200
        assert response.json()["face_sample_uuid"] == created

    def test_unknown_sample_returns_a_structured_404(self, client: TestClient) -> None:
        response = client.get(f"/api/v1/face-samples/{uuid4()}")
        assert response.status_code == 404
        body = ErrorResponse.model_validate(response.json())
        assert body.error.code == "face_sample_not_found"

    def test_a_malformed_uuid_fails_validation(self, client: TestClient) -> None:
        response = client.get("/api/v1/face-samples/not-a-uuid")
        assert response.status_code == 422
        assert ErrorResponse.model_validate(response.json()).error.code == "validation_error"


class TestSchemas:
    def test_openapi_documents_both_endpoints(self, client: TestClient) -> None:
        schema = client.get("/openapi.json").json()
        post = schema["paths"]["/api/v1/enrolments"]["post"]
        assert post["responses"]["202"]["content"]["application/json"]["schema"]
        assert "409" in post["responses"]
        get = schema["paths"]["/api/v1/face-samples/{face_sample_uuid}"]["get"]
        assert get["responses"]["200"]["content"]["application/json"]["schema"]
        assert "404" in get["responses"]
