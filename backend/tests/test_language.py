"""Dhivehi language primitives and authenticated HTTP contracts."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.api.v1.language import TextRequest, normalize
from app.core.config import Settings
from app.domain.auth import Principal, Scope
from app.domain.language import PrimaryScript, Script, TransliterationDirection
from app.main import create_app
from app.services.language import normalize_text, transliterate


def test_normalization_preserves_original_and_removes_unsafe_bidi_controls() -> None:
    result = normalize_text("ދިވެހި\u202e English\u00a0text")

    assert result.original == "ދިވެހި\u202e English\u00a0text"
    assert result.normalized == "ދިވެހި English text"
    assert result.primary_script is PrimaryScript.MIXED


def test_script_spans_retain_offsets_and_neutral_text() -> None:
    result = normalize_text("ދިވެހި + English 42")

    assert [(span.text, span.script) for span in result.spans] == [
        ("ދިވެހި", Script.THAANA),
        (" + ", Script.OTHER),
        ("English", Script.LATIN),
        (" ", Script.OTHER),
        ("42", Script.NUMBER),
    ]
    assert "".join(span.text for span in result.spans) == result.normalized


@pytest.mark.parametrize(
    ("source", "expected"),
    [("dhivehi", "ދިވެހި"), ("aharen", "އަހަރެން")],
)
def test_latin_to_thaana_baseline(source: str, expected: str) -> None:
    result = transliterate(source, TransliterationDirection.LATIN_TO_THAANA)

    assert result.output == expected
    assert result.warnings


def test_thaana_to_latin_baseline_preserves_english() -> None:
    result = transliterate("ދިވެހި + English", TransliterationDirection.THAANA_TO_LATIN)

    assert result.output == "dhivehi + English"
    assert result.warnings == ()


@pytest.mark.asyncio
async def test_normalization_endpoint_returns_declared_contract() -> None:
    principal = Principal(
        token_uuid=uuid4(),
        subject="linguist@example.com",
        kind="user",
        scopes=frozenset({Scope.LANGUAGE}),
    )

    response = await normalize(TextRequest(text="ދިވެހި English"), principal)

    assert response.primary_script is PrimaryScript.MIXED
    assert response.normalizer_version == "dv-normalizer-v1"


def test_application_exposes_language_contract(settings: Settings) -> None:
    paths = create_app(settings).openapi()["paths"]

    assert "/api/v1/nlp/normalize" in paths
    assert "/api/v1/nlp/transliterate" in paths
