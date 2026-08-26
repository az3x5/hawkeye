"""Language-domain values shared by Dhivehi text services and transports."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from app.domain.jobs import ProcessingState


class Script(StrEnum):
    """A script class observable without guessing a language."""

    THAANA = "thaana"
    LATIN = "latin"
    NUMBER = "number"
    OTHER = "other"


class PrimaryScript(StrEnum):
    """The useful high-level script composition of a text."""

    THAANA = "thaana"
    LATIN = "latin"
    MIXED = "mixed"
    NONE = "none"


class TransliterationDirection(StrEnum):
    """Supported explicit transliteration directions."""

    LATIN_TO_THAANA = "latin_to_thaana"
    THAANA_TO_LATIN = "thaana_to_latin"


@dataclass(frozen=True, slots=True)
class ScriptSpan:
    """One contiguous run of characters with the same script class."""

    text: str
    start: int
    end: int
    script: Script


@dataclass(frozen=True, slots=True)
class NormalizedText:
    """Original and canonical text plus lossless source-offset spans."""

    original: str
    normalized: str
    primary_script: PrimaryScript
    spans: tuple[ScriptSpan, ...]


@dataclass(frozen=True, slots=True)
class Transliteration:
    """One versioned rule-transliteration result."""

    original: str
    output: str
    direction: TransliterationDirection
    model_version: str
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LanguageDocument:
    """A searchable text document and its observable processing state."""

    title: str
    source: str
    original_text: str
    normalized_text: str
    primary_script: PrimaryScript
    content_sha256: str
    attributes: dict[str, Any]
    document_uuid: UUID = dataclass_field(default_factory=uuid4)
    processing_state: ProcessingState = ProcessingState.PENDING
    embedding_model: str | None = None
    embedding_version: str | None = None
    vector_collection: str | None = None
    failure_reason: str | None = None
    created_at: datetime = dataclass_field(default_factory=lambda: datetime.now(UTC))
    processed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class LanguageSearchHit:
    """A semantically similar document returned from the language index."""

    document_uuid: UUID
    title: str
    source: str
    text: str
    primary_script: PrimaryScript
    score: float
    attributes: dict[str, Any]
