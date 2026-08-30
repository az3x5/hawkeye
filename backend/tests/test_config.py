"""Settings are environment-driven and fail closed."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings

from .conftest import TEST_ENV


def test_settings_load_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in TEST_ENV.items():
        monkeypatch.setenv(key, value)
    settings = Settings()  # type: ignore[call-arg]
    assert settings.environment == "test"
    assert settings.qdrant_url == "http://qdrant:6333"
    assert settings.debug is False


def test_missing_required_dependency_config_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory
) -> None:
    for key in TEST_ENV:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)  # type: ignore[arg-type]
    with pytest.raises(ValidationError) as excinfo:
        Settings()  # type: ignore[call-arg]
    missing = {err["loc"][0] for err in excinfo.value.errors()}
    assert {"postgres_dsn", "redis_dsn"} <= missing


def test_no_credentials_are_defaulted_in_code(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in TEST_ENV.items():
        monkeypatch.setenv(key, value)
    settings = Settings()  # type: ignore[call-arg]
    assert settings.qdrant_api_key is None


def test_retry_ceiling_cannot_be_below_initial_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key, value in TEST_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("FACEID_JOB_RETRY_BASE_SECONDS", "60")
    monkeypatch.setenv("FACEID_JOB_RETRY_MAX_SECONDS", "30")
    with pytest.raises(ValidationError, match="job_retry_max_seconds"):
        Settings()  # type: ignore[call-arg]
