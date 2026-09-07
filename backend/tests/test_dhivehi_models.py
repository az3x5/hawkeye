"""Specialist Dhivehi model provenance and resource-governor contracts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from app.services.dhivehi_models import (
    MODEL_REGISTRY,
    DhivehiAISettings,
    DhivehiModelRuntime,
    DhivehiTask,
)


def test_every_public_specialist_model_is_pinned_and_licensed() -> None:
    for task, spec in MODEL_REGISTRY.items():
        if task is DhivehiTask.UNDERSTANDING:
            continue
        assert len(spec.revision) == 40
        assert spec.license in {"apache-2.0", "mit"}
        assert spec.status == "available"


def test_understanding_model_uses_public_resource_sized_runtime() -> None:
    model = MODEL_REGISTRY[DhivehiTask.UNDERSTANDING]

    assert model.status == "available"
    assert model.model_id == "qwen3:4b"
    assert model.runtime == "ollama"
    assert model.license == "apache-2.0"


def test_capability_metadata_never_claims_unqualified_accuracy() -> None:
    for spec in MODEL_REGISTRY.values():
        assert spec.quality_summary
        assert spec.limitation


def test_bot_owns_system_prompt_and_reports_installed_digest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, Any] = {}

    def fake_get(url: str, **_kwargs: object) -> httpx.Response:
        return httpx.Response(
            200,
            json={"models": [{"name": "qwen3:4b", "digest": "sha256:test-digest"}]},
            request=httpx.Request("GET", url),
        )

    def fake_post(url: str, **kwargs: object) -> httpx.Response:
        captured.update(kwargs)
        return httpx.Response(
            200,
            json={"model": "qwen3:4b", "message": {"content": "ރަނގަޅު"}},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "post", fake_post)
    settings = DhivehiAISettings(cache_dir=tmp_path, embedding_cache_dir=tmp_path)
    runtime = DhivehiModelRuntime(settings)

    result = runtime.chat([{"role": "user", "content": "How are you?"}], "dhivehi")

    payload = captured["json"]
    assert isinstance(payload, dict)
    assert payload["messages"][0]["role"] == "system"
    assert "Thaana" in payload["messages"][0]["content"]
    assert payload["messages"][1] == {"role": "user", "content": "How are you?"}
    assert result["text"] == "ރަނގަޅު"
    assert result["model_revision"] == "sha256:test-digest"
