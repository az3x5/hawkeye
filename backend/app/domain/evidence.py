"""Versioned contracts for source-linked, non-identifying evidence analysis."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

PIPELINE = "evidence_analysis"
PIPELINE_VERSION = "1.0"
DELIVERY_PIPELINE = "evidence_delivery"


class Contract(BaseModel):
    """Reject silently ignored fields in integration requests."""

    model_config = ConfigDict(extra="forbid")


class EvidenceSource(Contract):
    """One BlackGlass record, independent of the content's hash."""

    system: str = Field(default="blackglass-prod", min_length=1, max_length=128)
    object_type: str = Field(min_length=1, max_length=64)
    object_id: str = Field(min_length=1, max_length=256)
    source_url: str | None = Field(default=None, max_length=2048)
    collected_at: datetime | None = None


class AnalysisOptions(Contract):
    """Bound the work requested for one evidence submission."""

    language: Literal["dv", "en", "mixed", "unknown"] = "unknown"
    translate_to: Literal["en", "dv"] | None = None
    transliterate: Literal["latin", "thaana"] | None = None
    summarize: bool = True
    max_pages: int = Field(default=20, ge=1, le=100)
    max_seconds: int = Field(default=120, ge=1, le=600)
    max_frames: int = Field(default=6, ge=1, le=20)


class StreamSegment(Contract):
    """An uploaded finite segment; timestamps are relative to this segment."""

    stream_id: str = Field(min_length=1, max_length=128)
    segment_id: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=0)
    started_at: datetime

    @model_validator(mode="after")
    def timezone_required(self) -> StreamSegment:
        """Reject stream clocks that cannot be aligned to UTC."""
        if self.started_at.tzinfo is None:
            raise ValueError("stream.started_at requires a timezone")
        return self


class Submission(Contract):
    """Metadata used by text and multipart file submissions."""

    schema_version: Literal["1.0"] = "1.0"
    source: EvidenceSource
    options: AnalysisOptions = Field(default_factory=AnalysisOptions)
    stream: StreamSegment | None = None


class TextSubmission(Submission):
    """UTF-8 text is preserved exactly before normalization."""

    text: str = Field(min_length=1, max_length=100_000)


class SharedObject(Contract):
    """A retained original in the server-configured BlackGlass AWS bucket."""

    key: str = Field(min_length=1, max_length=1024)
    version_id: str | None = Field(default=None, max_length=1024)
    sha256: str = Field(pattern="^[0-9a-f]{64}$")
    byte_size: int = Field(gt=0, le=50 * 1024**2)
    mime_type: str = Field(min_length=1, max_length=128)


class ObjectSubmission(Submission):
    """Ingest a manifest without creating another original-file copy."""

    object: SharedObject


class ObjectBatch(Contract):
    """Bounded bulk manifest intake; each source record receives its own outcome."""

    items: list[ObjectSubmission] = Field(min_length=1, max_length=50)


class Locator(Contract):
    """Exact source region; offsets are Unicode codepoints, end-exclusive."""

    page: int | None = Field(default=None, ge=1)
    char_start: int | None = Field(default=None, ge=0)
    char_end: int | None = Field(default=None, ge=0)
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, ge=0)
    precision: str = "source"

    @model_validator(mode="after")
    def ordered(self) -> Locator:
        """Do not emit inverted or incomplete ranges."""
        for a, b in ((self.char_start, self.char_end), (self.start_ms, self.end_ms)):
            if (a is None) != (b is None) or (a is not None and b is not None and b < a):
                raise ValueError("locator ranges require ordered start and end")
        return self


class EvidencePiece(Contract):
    """Extracted text or observation with original source and model provenance."""

    evidence_id: UUID
    kind: Literal["text", "document_text", "ocr", "transcript", "visual_observation"]
    original_text: str
    normalized_text: str
    locator: Locator
    provenance: dict[str, str]
    translations: list[dict[str, str]] = Field(default_factory=list)
    review_status: Literal["unreviewed"] = "unreviewed"


class Citation(Contract):
    """A verbatim excerpt supporting an unreviewed generated finding."""

    evidence_id: UUID
    quote: str = Field(min_length=1, max_length=2000)


class Finding(Contract):
    """A model assertion; citation validation alone does not establish truth."""

    statement: str = Field(min_length=1, max_length=2000)
    citations: list[Citation] = Field(min_length=1, max_length=8)
    review_status: Literal["unreviewed"] = "unreviewed"


class Findings(Contract):
    """Bounded structured response expected from the language model."""

    findings: list[Finding] = Field(default_factory=list, max_length=20)
    contradictions: list[Finding] = Field(default_factory=list, max_length=10)


def validate_citations(result: Findings, evidence: list[EvidencePiece]) -> None:
    """Reject invented evidence IDs and quotes, including cross-run references."""
    originals = {piece.evidence_id: piece.original_text for piece in evidence}
    for finding in result.findings + result.contradictions:
        for citation in finding.citations:
            if citation.evidence_id not in originals:
                raise ValueError("model cited evidence outside this analysis")
            if citation.quote not in originals[citation.evidence_id]:
                raise ValueError("model citation is not a verbatim source excerpt")


def normalize(text: str) -> str:
    """Normalize Unicode and whitespace without rewriting or translating words."""
    return " ".join(unicodedata.normalize("NFC", text).split())


def submission_key(owner: str, submission: Submission, sha256: str) -> str:
    """Keep identical content submitted for different BlackGlass records distinct."""
    value = {
        "owner": owner,
        "submission": submission.model_dump(mode="json", exclude={"text"}),
        "sha256": sha256,
        "pipeline_version": PIPELINE_VERSION,
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
