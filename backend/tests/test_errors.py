"""Every failure leaves the service in the structured error envelope."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.core.errors import ErrorResponse, FaceIdError, ServiceUnavailableError


class _Body(BaseModel):
    count: int


@pytest.fixture
def error_client(app: FastAPI) -> TestClient:
    @app.post("/api/v1/_test/echo")
    async def echo(body: _Body) -> _Body:  # pragma: no cover - exercised via HTTP
        return body

    @app.get("/api/v1/_test/boom")
    async def boom() -> None:  # pragma: no cover - exercised via HTTP
        raise ServiceUnavailableError("qdrant is down")

    return TestClient(app)


def test_unknown_route_uses_the_error_envelope(client: TestClient) -> None:
    response = client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    body = ErrorResponse.model_validate(response.json())
    assert body.error.code == "http_404"


def test_validation_failure_names_the_offending_field(error_client: TestClient) -> None:
    response = error_client.post("/api/v1/_test/echo", json={"count": "not-a-number"})
    assert response.status_code == 422
    body = ErrorResponse.model_validate(response.json())
    assert body.error.code == "validation_error"
    assert [d.field for d in body.details] == ["count"]


def test_domain_error_maps_to_its_status_and_code(error_client: TestClient) -> None:
    response = error_client.get("/api/v1/_test/boom")
    assert response.status_code == 503
    body = ErrorResponse.model_validate(response.json())
    assert body.error.code == "service_unavailable"
    assert body.error.message == "qdrant is down"


def test_base_error_defaults_to_internal_error() -> None:
    payload = FaceIdError("unexpected").to_response()
    assert payload.error.code == "internal_error"
    assert FaceIdError.status_code == 500
