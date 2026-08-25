"""Unicode-safe Dhivehi normalization, detection and baseline transliteration."""

from __future__ import annotations

import re
import unicodedata

from app.domain.language import (
    NormalizedText,
    PrimaryScript,
    Script,
    ScriptSpan,
    Transliteration,
    TransliterationDirection,
)

NORMALIZER_VERSION = "dv-normalizer-v1"
TRANSLITERATOR_VERSION = "dv-rules-v1"

_BIDI_CONTROLS = {
    "\u061c",
    "\u200e",
    "\u200f",
    "\u202a",
    "\u202b",
    "\u202c",
    "\u202d",
    "\u202e",
    "\u2066",
    "\u2067",
    "\u2068",
    "\u2069",
}
_LATIN_WORD = re.compile(r"[A-Za-z]+(?:['’-][A-Za-z]+)*")

_THAANA_CONSONANTS = {
    "ހ": "h",
    "ށ": "sh",
    "ނ": "n",
    "ރ": "r",
    "ބ": "b",
    "ޅ": "lh",
    "ކ": "k",
    "އ": "",
    "ވ": "v",
    "މ": "m",
    "ފ": "f",
    "ދ": "dh",
    "ތ": "th",
    "ލ": "l",
    "ގ": "g",
    "ޏ": "gn",
    "ސ": "s",
    "ޑ": "d",
    "ޒ": "z",
    "ޓ": "t",
    "ޔ": "y",
    "ޕ": "p",
    "ޖ": "j",
    "ޗ": "ch",
    "ޘ": "th",
    "ޙ": "h",
    "ޚ": "kh",
    "ޛ": "dh",
    "ޜ": "z",
    "ޝ": "sh",
    "ޞ": "s",
    "ޟ": "d",
    "ޠ": "t",
    "ޡ": "z",
    "ޢ": "'",
    "ޣ": "gh",
    "ޤ": "q",
    "ޥ": "w",
}
_THAANA_VOWELS = {
    "ަ": "a",
    "ާ": "aa",
    "ި": "i",
    "ީ": "ee",
    "ު": "u",
    "ޫ": "oo",
    "ެ": "e",
    "ޭ": "ey",
    "ޮ": "o",
    "ޯ": "oa",
    "ް": "",
}
_LATIN_CONSONANTS = {
    "sh": "ށ",
    "lh": "ޅ",
    "dh": "ދ",
    "th": "ތ",
    "gn": "ޏ",
    "ch": "ޗ",
    "kh": "ޚ",
    "gh": "ޣ",
    "h": "ހ",
    "n": "ނ",
    "r": "ރ",
    "b": "ބ",
    "k": "ކ",
    "v": "ވ",
    "w": "ވ",
    "m": "މ",
    "f": "ފ",
    "l": "ލ",
    "g": "ގ",
    "s": "ސ",
    "c": "ސ",
    "d": "ޑ",
    "z": "ޒ",
    "t": "ޓ",
    "y": "ޔ",
    "p": "ޕ",
    "j": "ޖ",
    "q": "ޤ",
}
_LATIN_VOWELS = {
    "aa": "ާ",
    "ee": "ީ",
    "oo": "ޫ",
    "ey": "ޭ",
    "oa": "ޯ",
    "a": "ަ",
    "i": "ި",
    "u": "ު",
    "e": "ެ",
    "o": "ޮ",
}


def normalize_text(text: str) -> NormalizedText:
    """Normalize canonical Unicode while retaining the exact original text."""
    canonical = unicodedata.normalize("NFC", text.replace("\u00a0", " "))
    canonical = "".join(character for character in canonical if character not in _BIDI_CONTROLS)
    spans = tuple(detect_script_spans(canonical))
    has_thaana = any(span.script is Script.THAANA for span in spans)
    has_latin = any(span.script is Script.LATIN for span in spans)
    if has_thaana and has_latin:
        primary = PrimaryScript.MIXED
    elif has_thaana:
        primary = PrimaryScript.THAANA
    elif has_latin:
        primary = PrimaryScript.LATIN
    else:
        primary = PrimaryScript.NONE
    return NormalizedText(text, canonical, primary, spans)


def detect_script_spans(text: str) -> list[ScriptSpan]:
    """Split text into contiguous Thaana, Latin, number and neutral runs."""
    if not text:
        return []
    spans: list[ScriptSpan] = []
    start = 0
    current = _script_of(text[0])
    for index, character in enumerate(text[1:], start=1):
        script = _script_of(character)
        if script is current:
            continue
        spans.append(ScriptSpan(text[start:index], start, index, current))
        start = index
        current = script
    spans.append(ScriptSpan(text[start:], start, len(text), current))
    return spans


def transliterate(text: str, direction: TransliterationDirection) -> Transliteration:
    """Apply the versioned baseline rules in one explicit direction."""
    normalized = normalize_text(text).normalized
    if direction is TransliterationDirection.THAANA_TO_LATIN:
        output = _thaana_to_latin(normalized)
        warnings: tuple[str, ...] = ()
    else:
        output = _LATIN_WORD.sub(lambda match: _latin_word_to_thaana(match.group()), normalized)
        warnings = (
            "Latin-to-Thaana is phonetic and ambiguous; review names and English words.",
        )
    return Transliteration(
        original=text,
        output=output,
        direction=direction,
        model_version=TRANSLITERATOR_VERSION,
        warnings=warnings,
    )


def _script_of(character: str) -> Script:
    point = ord(character)
    if 0x0780 <= point <= 0x07BF:
        return Script.THAANA
    if character.isascii() and character.isalpha():
        return Script.LATIN
    if character.isdigit():
        return Script.NUMBER
    return Script.OTHER


def _thaana_to_latin(text: str) -> str:
    output: list[str] = []
    for character in text:
        if character in _THAANA_CONSONANTS:
            output.append(_THAANA_CONSONANTS[character])
        elif character in _THAANA_VOWELS:
            output.append(_THAANA_VOWELS[character])
        else:
            output.append(character)
    return "".join(output)


def _latin_word_to_thaana(word: str) -> str:
    source = word.lower().replace("’", "'")
    output: list[str] = []
    index = 0
    while index < len(source):
        if source[index] in "'-":
            output.append(source[index])
            index += 1
            continue

        vowel, consumed = _match(source, index, _LATIN_VOWELS)
        if vowel is not None:
            output.extend(("އ", vowel))
            index += consumed
            continue

        consonant, consumed = _match(source, index, _LATIN_CONSONANTS)
        if consonant is None:
            output.append(source[index])
            index += 1
            continue
        output.append(consonant)
        index += consumed
        vowel, consumed = _match(source, index, _LATIN_VOWELS)
        if vowel is None:
            output.append("ް")
        else:
            output.append(vowel)
            index += consumed
    return "".join(output)


def _match(mapping_source: str, index: int, mapping: dict[str, str]) -> tuple[str | None, int]:
    for length in (2, 1):
        candidate = mapping_source[index : index + length]
        if candidate in mapping:
            return mapping[candidate], length
    return None, 0
