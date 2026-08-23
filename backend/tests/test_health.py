"""Contract tests for the system endpoints."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.health import HealthResponse, ReadinessResponse
from app.core.readiness import register_probe


def test_health_returns_declared_schema(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = HealthResponse.model_validate(response.json())
    assert body.status == "ok"
    assert body.service == "hawkeye"
    assert body.environment == "test"


def test_health_is_registered_under_the_versioned_prefix(client: TestClient) -> None:
    assert client.get("/health").status_code == 404


def test_readyz_reports_no_checks_when_nothing_is_registered(client: TestClient) -> None:
    response = client.get("/api/v1/readyz")
    assert response.status_code == 200
    body = ReadinessResponse.model_validate(response.json())
    assert body.status == "ready"
    assert body.checks == []


def test_readyz_reports_a_registered_healthy_probe(probe_app: FastAPI) -> None:
    async def ok() -> None:
        return None

    register_probe("postgres", ok)
    with TestClient(probe_app) as client:
        body = ReadinessResponse.model_validate(client.get("/api/v1/readyz").json())
    assert body.status == "ready"
    assert [(c.name, c.healthy) for c in body.checks] == [("postgres", True)]


def test_readyz_returns_503_and_the_reason_when_a_probe_fails(probe_app: FastAPI) -> None:
    async def broken() -> None:
        raise ConnectionError("qdrant unreachable")

    register_probe("qdrant", broken)
    with TestClient(probe_app) as client:
        response = client.get("/api/v1/readyz")
    assert response.status_code == 503
    body = ReadinessResponse.model_validate(response.json())
    assert body.status == "not_ready"
    assert body.checks[0].name == "qdrant"
    assert body.checks[0].healthy is False
    assert "qdrant unreachable" in (body.checks[0].error or "")


def test_openapi_declares_schemas_for_every_endpoint(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    for path in ("/api/v1/health", "/api/v1/readyz"):
        operation = schema["paths"][path]["get"]
        assert operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert "ErrorResponse" in schema["components"]["schemas"]
