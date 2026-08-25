"""Language-domain values shared by Dhivehi text services and transports."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


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
