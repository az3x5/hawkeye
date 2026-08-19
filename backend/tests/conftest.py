"""Shared test fixtures."""

from __future__ import annotations

import os
from collections.abc import Iterator
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.health import router as health_router
from app.api.v1.security import get_principal
from app.core.config import Settings
from app.core.errors import install_error_handlers
from app.core.readiness import clear_probes
from app.domain.auth import Principal, Scope
from app.main import create_app

#: Integration tests run only when a real database is pointed at explicitly.
INTEGRATION_DSN = os.environ.get("FACEID_TEST_POSTGRES_DSN")

#: Fixtures here TRUNCATE whole tables. Pointing them at a working database
#: destroys real data, which has happened more than once during development, so
#: the suite refuses to run unless the database is named as a test one.
_TEST_DATABASE_SUFFIX = "_test"


def _require_disposable_database() -> None:
    if INTEGRATION_DSN is None:
        return
    name = urlsplit(INTEGRATION_DSN).path.lstrip("/")
    if not name.endswith(_TEST_DATABASE_SUFFIX):
        raise pytest.UsageError(
            f"FACEID_TEST_POSTGRES_DSN points at database {name!r}. These tests "
            f"TRUNCATE tables, so the name must end in {_TEST_DATABASE_SUFFIX!r} "
            "to make clear it is disposable. Create one with:\n"
            "  createdb faceid_test   (or: CREATE DATABASE faceid_test;)"
        )


_require_disposable_database()

TEST_ENV = {
    "FACEID_ENVIRONMENT": "test",
    "FACEID_POSTGRES_DSN": "postgresql://faceid:unused@localhost:5432/faceid",
    "FACEID_REDIS_DSN": "redis://localhost:6379/0",
    "FACEID_QDRANT_URL": "http://qdrant:6333",
    "FACEID_DECISION_ACCEPT_THRESHOLD": "0.62",
    "FACEID_DECISION_REVIEW_THRESHOLD": "0.42",
    "FACEID_DECISION_POLICY_VERSION": "test-policy-v1",
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


def authenticate(
    app: FastAPI, *scopes: Scope, subject: str = "tester@example.com", kind: str = "user"
) -> Principal:
    """Give an app a fixed authenticated principal, for endpoint contract tests.

    Authentication itself is covered in ``test_auth.py``; tests of *other*
    endpoints should not have to mint credentials to reach them.
    """
    principal = Principal(
        token_uuid=uuid4(),
        subject=subject,
        kind=kind,
        scopes=frozenset(scopes),
    )
    app.dependency_overrides[get_principal] = lambda: principal
    return principal
