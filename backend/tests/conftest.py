"""Shared test fixtures."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.readiness import clear_probes
from app.main import create_app

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
    clear_probes()
    yield create_app(settings)
    clear_probes()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client
