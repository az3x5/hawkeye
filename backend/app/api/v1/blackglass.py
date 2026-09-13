"""Versioned BlackGlass delivery contract owned by EagleEye.

BlackGlass pushes collected material through these endpoints. It never writes
EagleEye tables and EagleEye never treats an external identifier as its own
primary key. Binary evidence and text share provenance semantics while using
the storage service appropriate to each content type.
"""

from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from pydantic import BaseModel, Field, field_validator

from app.api.v1.dependencies import get_language_document_service, get_media_service
from app.api.v1.media import (
    AssetSourceResponse,
    InvalidMediaError,
    MediaAssetResponse,
    MediaTooLargeResponseError,
    read_bounded_upload,
)
from app.api.v1.security import require
from app.core.errors import ErrorResponse
from app.domain.auth import Principal, Scope
from app.domain.content_types import MediaTooLargeError, UnsupportedMediaError
from app.domain.jobs import ProcessingState
from app.domain.media import Classification, MediaError, SourceType
from app.services.language_search import DocumentSubmission, LanguageDocumentService
from app.services.media import IngestRequest, MediaService

router = APIRouter(prefix="/integrations/blackglass", tags=["integrations", "blackglass"])


class AnalysisCapability(StrEnum):
    """Stable analysis names a collector may request."""

    FACE_DETECTION = "face_detection"
    FACE_IDENTIFICATION = "face_identification"
    OBJECT_DETECTION = "object_detection"
    VEHICLE_DETECTION = "vehicle_detection"
    VEHICLE_IDENTIFICATION = "vehicle_identification"
    LANDMARK_RECOGNITION = "landmark_recognition"
    VIDEO_TRACKING = "video_tracking"
    OCR = "ocr"
    TRANSCRIPTION = "transcription"
    TRANSLATION = "translation"
    TRANSLITERATION = "transliteration"
    SEMANTIC_EMBEDDING = "semantic_embedding"


class AnalysisRoute(BaseModel):
    """What EagleEye can currently do with one requested analysis."""

    capability: AnalysisCapability
    state: Literal["queued", "available_on_demand", "not_connected", "not_applicable"]
    endpoint: str | None = None
    detail: str


class BlackGlassSource(BaseModel):
    """External identity and collection facts retained with content."""

    system: str = Field(default="blackglass-prod", min_length=1, max_length=128)
    object_type: str = Field(min_length=1, max_length=64)
    object_id: str = Field(min_length=1, max_length=256)
    source_url: str | None = Field(default=None, max_length=2048)
    collected_at: datetime | None = None
    published_at: datetime | None = None
    collector_version: str | None = Field(default=None, max_length=128)


class BlackGlassTextRequest(BaseModel):
    """One BlackGlass text object submitted for search and later enrichment."""

    schema_version: Literal["1.0"] = "1.0"
    source: BlackGlassSource
    title: str = Field(min_length=1, max_length=256)
    text: str = Field(min_length=1, max_length=100_000)
    language_hint: str | None = Field(default=None, max_length=16)
    attributes: dict[str, Any] = Field(default_factory=dict)
    requested_analyses: list[AnalysisCapability] = Field(
        default_factory=lambda: [AnalysisCapability.SEMANTIC_EMBEDDING]
    )

    @field_validator("requested_analyses")
    @classmethod
    def unique_analyses(cls, value: list[AnalysisCapability]) -> list[AnalysisCapability]:
        """Avoid ambiguous duplicate work in one request."""
        if len(value) != len(set(value)):
            raise ValueError("requested_analyses must not contain duplicates")
        return value


class IngestedSubject(BaseModel):
    """Stable EagleEye identifier created or reused by a delivery."""

    type: Literal["media", "language_document"]
    id: UUID


class BlackGlassIngestionResponse(BaseModel):
    """Shared acknowledgement for text and binary deliveries."""

    schema_version: Literal["1.0"] = "1.0"
    source: BlackGlassSource
    subject: IngestedSubject
    status: Literal["accepted", "already_exists"]
    created: bool
    content_type: str
    analysis_routes: list[AnalysisRoute]
    media: MediaAssetResponse | None = None
    media_source: AssetSourceResponse | None = None
    processing_state: ProcessingState | None = None


class BlackGlassCapabilityResponse(BaseModel):
    """Contract-level availability without claiming a worker is running."""

    schema_version: Literal["1.0"] = "1.0"
    delivery_endpoints: dict[str, str]
    analyses: list[AnalysisRoute]
    guarantees: list[str]


def _requested(value: str) -> list[AnalysisCapability]:
    """Parse a JSON array or comma-separated multipart field."""
    if not value.strip():
        return []
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        decoded = [item.strip() for item in value.split(",") if item.strip()]
    if not isinstance(decoded, list):
        raise InvalidMediaError("requested_analyses must be an array or comma-separated list")
    try:
        parsed = [AnalysisCapability(str(item)) for item in decoded]
    except ValueError as exc:
        raise InvalidMediaError(f"unknown analysis capability: {exc}") from exc
    if len(parsed) != len(set(parsed)):
        raise InvalidMediaError("requested_analyses must not contain duplicates")
    return parsed


def _default_analyses(content_type: str) -> list[AnalysisCapability]:
    """Choose useful defaults only after magic-byte type detection."""
    media_type = content_type.split("/", 1)[0]
    if media_type == "image":
        return [AnalysisCapability.OBJECT_DETECTION, AnalysisCapability.OCR]
    if media_type == "video":
        return [
            AnalysisCapability.OBJECT_DETECTION,
            AnalysisCapability.VIDEO_TRACKING,
            AnalysisCapability.TRANSCRIPTION,
        ]
    if media_type == "audio":
        return [AnalysisCapability.TRANSCRIPTION]
    if content_type == "application/pdf":
        return [AnalysisCapability.OCR]
    return []


def _route(capability: AnalysisCapability, content_type: str) -> AnalysisRoute:
    """Describe only processing paths that actually exist today."""
    media_type = content_type.split("/", 1)[0]
    applicable: dict[AnalysisCapability, set[str]] = {
        AnalysisCapability.FACE_DETECTION: {"image", "video"},
        AnalysisCapability.FACE_IDENTIFICATION: {"image"},
        AnalysisCapability.OBJECT_DETECTION: {"image", "video"},
        AnalysisCapability.VEHICLE_DETECTION: {"image", "video"},
        AnalysisCapability.VEHICLE_IDENTIFICATION: {"image", "video"},
        AnalysisCapability.LANDMARK_RECOGNITION: {"image", "video"},
        AnalysisCapability.VIDEO_TRACKING: {"video"},
        AnalysisCapability.OCR: {"image", "application"},
        AnalysisCapability.TRANSCRIPTION: {"audio", "video"},
        AnalysisCapability.TRANSLATION: {"text", "audio", "video", "image", "application"},
        AnalysisCapability.TRANSLITERATION: {"text", "audio", "video", "image", "application"},
        AnalysisCapability.SEMANTIC_EMBEDDING: {"text"},
    }
    if media_type not in applicable[capability]:
        return AnalysisRoute(
            capability=capability,
            state="not_applicable",
            detail=f"{capability.value} does not apply to {content_type}",
        )
    on_demand = {
        AnalysisCapability.FACE_DETECTION: "/api/v1/identifications",
        AnalysisCapability.FACE_IDENTIFICATION: "/api/v1/identifications",
        AnalysisCapability.OCR: "/api/v1/nlp/ocr",
        AnalysisCapability.TRANSCRIPTION: "/api/v1/nlp/speech/transcribe",
        AnalysisCapability.TRANSLATION: "/api/v1/nlp/infer",
        AnalysisCapability.TRANSLITERATION: "/api/v1/nlp/infer",
    }
    if capability in on_demand:
        return AnalysisRoute(
            capability=capability,
            state="available_on_demand",
            endpoint=on_demand[capability],
            detail=(
                "Available through the named authenticated API; automatic media routing is next."
            ),
        )
    if capability is AnalysisCapability.SEMANTIC_EMBEDDING:
        return AnalysisRoute(
            capability=capability,
            state="queued",
            endpoint="/api/v1/nlp/documents/{document_uuid}",
            detail="Text embedding is persisted in the durable language queue.",
        )
    return AnalysisRoute(
        capability=capability,
        state="not_connected",
        detail=(
            "The model artifact may be installed, but no production result worker is connected yet."
        ),
    )


@router.get(
    "/capabilities",
    response_model=BlackGlassCapabilityResponse,
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}},
)
async def capabilities(
    _principal: Annotated[Principal, Depends(require(Scope.MEDIA_WRITE))],
) -> BlackGlassCapabilityResponse:
    """Return the stable delivery contract and honest processing routes."""
    return BlackGlassCapabilityResponse(
        delivery_endpoints={
            "media": "/api/v1/integrations/blackglass/media",
            "text": "/api/v1/integrations/blackglass/text",
        },
        analyses=[
            _route(
                item,
                {
                    AnalysisCapability.TRANSCRIPTION: "audio/wav",
                    AnalysisCapability.SEMANTIC_EMBEDDING: "text/plain",
                    AnalysisCapability.VIDEO_TRACKING: "video/mp4",
                }.get(item, "image/jpeg"),
            )
            for item in AnalysisCapability
        ],
        guarantees=[
            "content-addressed binary storage",
            "idempotent external provenance",
            "bounded uploads and magic-byte type validation",
            "authenticated and audited delivery",
            "AI candidates are not confirmed identities",
        ],
    )


@router.post(
    "/media",
    response_model=BlackGlassIngestionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        413: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def ingest_blackglass_media(
    file: Annotated[UploadFile, File()],
    external_object_id: Annotated[str, Form(min_length=1, max_length=256)],
    external_object_type: Annotated[str, Form(min_length=1, max_length=64)],
    service: Annotated[MediaService, Depends(get_media_service)],
    principal: Annotated[Principal, Depends(require(Scope.MEDIA_WRITE))],
    source_system: Annotated[str, Form(min_length=1, max_length=128)] = "blackglass-prod",
    classification: Annotated[Classification, Form()] = Classification.INTERNAL,
    source_url: Annotated[str | None, Form(max_length=2048)] = None,
    collected_at: Annotated[datetime | None, Form()] = None,
    published_at: Annotated[datetime | None, Form()] = None,
    collector_version: Annotated[str | None, Form(max_length=128)] = None,
    requested_analyses: Annotated[str, Form()] = "",
) -> BlackGlassIngestionResponse:
    """Store one BlackGlass binary object and return its processing routes."""
    try:
        requested = _requested(requested_analyses)
        data = await read_bounded_upload(file, service.max_bytes)
        result = await service.ingest(
            IngestRequest(
                data=data,
                source_type=SourceType.BLACKGLASS,
                source_system=source_system,
                declared_content_type=file.content_type,
                classification=classification,
                external_source_id=external_object_id,
                source_url=source_url,
                collected_at=collected_at,
                published_at=published_at,
                collector_version=collector_version,
                submitted_by=principal.subject,
            )
        )
    except MediaTooLargeError as exc:
        raise MediaTooLargeResponseError(str(exc), field="file") from exc
    except UnsupportedMediaError as exc:
        raise InvalidMediaError(str(exc), field="file") from exc
    except MediaError as exc:
        raise InvalidMediaError(str(exc)) from exc

    source = BlackGlassSource(
        system=source_system,
        object_type=external_object_type,
        object_id=external_object_id,
        source_url=source_url,
        collected_at=collected_at,
        published_at=published_at,
        collector_version=collector_version,
    )
    if not requested:
        requested = _default_analyses(result.asset.mime_type)
    return BlackGlassIngestionResponse(
        source=source,
        subject=IngestedSubject(type="media", id=result.asset.media_uuid),
        status="accepted" if result.created else "already_exists",
        created=result.created,
        content_type=result.asset.mime_type,
        analysis_routes=[_route(item, result.asset.mime_type) for item in requested],
        media=MediaAssetResponse.model_validate(result.asset, from_attributes=True),
        media_source=AssetSourceResponse.model_validate(result.source, from_attributes=True),
    )


@router.post(
    "/text",
    response_model=BlackGlassIngestionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}},
)
async def ingest_blackglass_text(
    body: BlackGlassTextRequest,
    service: Annotated[LanguageDocumentService, Depends(get_language_document_service)],
    _principal: Annotated[Principal, Depends(require(Scope.LANGUAGE))],
) -> BlackGlassIngestionResponse:
    """Persist BlackGlass text and enqueue semantic indexing idempotently."""
    attributes = {
        **body.attributes,
        "blackglass": body.source.model_dump(mode="json"),
        "language_hint": body.language_hint,
    }
    document, created = await service.submit(
        DocumentSubmission(
            title=body.title,
            source=body.source.system,
            text=body.text,
            attributes=attributes,
        )
    )
    return BlackGlassIngestionResponse(
        source=body.source,
        subject=IngestedSubject(type="language_document", id=document.document_uuid),
        status="accepted" if created else "already_exists",
        created=created,
        content_type="text/plain; charset=utf-8",
        analysis_routes=[_route(item, "text/plain") for item in body.requested_analyses],
        processing_state=document.processing_state,
    )
