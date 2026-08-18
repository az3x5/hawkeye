"""Shared test fixtures."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.health import router as health_router
from app.core.config import Settings
from app.core.errors import install_error_handlers
from app.core.readiness import clear_probes
from app.main import create_app

#: Integration tests run only when a real database is pointed at explicitly.
INTEGRATION_DSN = os.environ.get("FACEID_TEST_POSTGRES_DSN")

TEST_ENV = {
    "FACEID_ENVIRONMENT": "test",
    "FACEID_POSTGRES_DSN": "postgresql://faceid:unused@localhost:5432/faceid",
    "FACEID_REDIS_DSN": "redis://localhost:6379/0",
    "FACEID_QDRANT_URL": "http://qdrant:6333",
}


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    for key, value in TEST_ENV.items():
        monkeypatch.setenv(key, value)
    return Settings()  # type: ignore[call-arg]


@pytest.fixture
def app(settings: Settings) -> Iterator[FastAPI]:
    """The full application, including its startup wiring."""
    clear_probes()
    yield create_app(settings)
    clear_probes()


@pytest.fixture
def probe_app(settings: Settings) -> Iterator[FastAPI]:
    """The system router alone, with an empty readiness registry.

    Lets the readiness contract be tested without the startup wiring that
    registers real storage probes.
    """
    clear_probes()
    bare = FastAPI()
    install_error_handlers(bare)
    bare.include_router(health_router, prefix=settings.api_v1_prefix)
    yield bare
    clear_probes()


@pytest.fixture
def client(probe_app: FastAPI) -> Iterator[TestClient]:
    """HTTP client for contract tests that need no storage."""
    with TestClient(probe_app) as test_client:
        yield test_client
