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

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select

from app.api.v1.dependencies import (
    _postgres,
    get_blackglass_s3_source,
    get_enrolment_service,
    get_language_document_service,
    get_media_service,
    get_read_queries,
)
from app.api.v1.enrolments import MAX_IMAGE_BYTES
from app.api.v1.media import (
    AssetSourceResponse,
    InvalidMediaError,
    MediaAssetResponse,
    MediaTooLargeResponseError,
    read_bounded_upload,
)
from app.api.v1.security import require
from app.connectors.postgres.queries import ReadQueries
from app.connectors.postgres.tables import language_documents
from app.connectors.s3 import BlackGlassS3Error, BlackGlassS3Source
from app.core.errors import ErrorResponse, FaceIdError
from app.domain.auth import Principal, Scope
from app.domain.content_types import (
    IMAGE_FORMATS,
    MediaTooLargeError,
    UnsupportedMediaError,
    sniff,
    verify_declared,
)
from app.domain.evidence import AnalysisOptions, ReportSubject, TextSubmission
from app.domain.jobs import ProcessingState
from app.domain.media import Classification, MediaError, SourceType
from app.domain.models import DomainValidationError
from app.services.enrolment import EnrolmentError, EnrolmentRequest, EnrolmentService
from app.services.evidence import EvidenceRepository, content_hash
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


class BlackGlassTextRequest(BaseModel):
    """One BlackGlass text object submitted for search and later enrichment."""

    schema_version: Literal["1.0", "1.1"] = "1.1"
    source_id: str = Field(min_length=1, max_length=256)
    source_type: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=256)
    text: str = Field(min_length=1, max_length=100_000)
    language_hint: str | None = Field(default=None, max_length=16)
    attributes: dict[str, Any] = Field(default_factory=dict)
    requested_analyses: list[AnalysisCapability] = Field(
        default_factory=lambda: [AnalysisCapability.SEMANTIC_EMBEDDING]
    )

    @model_validator(mode="before")
    @classmethod
    def flatten_legacy_source(cls, value: Any) -> Any:
        """Normalize the old source object without returning or persisting it."""
        if not isinstance(value, dict) or not isinstance(value.get("source"), dict):
            return value
        source = dict(value["source"])
        flattened = dict(value)
        flattened.pop("source", None)
        flattened["schema_version"] = "1.1"
        flattened.setdefault("source_id", source.get("object_id"))
        flattened.setdefault("source_type", source.get("object_type"))
        attributes = dict(flattened.get("attributes") or {})
        for old, new in {
            "system": "source_system",
            "source_url": "source_url",
            "collected_at": "collected_at",
            "published_at": "published_at",
            "collector_version": "collector_version",
        }.items():
            if source.get(old) is not None:
                attributes.setdefault(new, source[old])
        flattened["attributes"] = attributes
        return flattened

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

    schema_version: Literal["1.1"] = "1.1"
    source_id: str
    source_type: str
    attributes: dict[str, Any] = Field(default_factory=dict)
    subject: IngestedSubject
    status: Literal["accepted", "already_exists"]
    created: bool
    content_type: str
    analysis_routes: list[AnalysisRoute]
    media: MediaAssetResponse | None = None
    media_source: AssetSourceResponse | None = None
    processing_state: ProcessingState | None = None


class BlackGlassDocumentSummary(BaseModel):
    """One imported record suitable for monitoring without exposing its text."""

    document_uuid: UUID
    source_id: str
    source_type: str
    profile_id: str | None
    title: str
    source: str
    primary_script: str
    processing_state: ProcessingState
    attributes: dict[str, Any]
    created_at: datetime
    processed_at: datetime | None


class BlackGlassDocumentPage(BaseModel):
    """A bounded page of imported BlackGlass text records."""

    items: list[BlackGlassDocumentSummary]
    total: int
    limit: int
    offset: int
    profiles: dict[str, int]


class BlackGlassProfileNotFoundError(FaceIdError):
    """No imported text belongs to the requested profile."""

    status_code = status.HTTP_404_NOT_FOUND
    code = "blackglass_profile_not_found"


class BlackGlassProfileTooLargeError(FaceIdError):
    """The complete profile corpus exceeds one bounded evidence run."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "blackglass_profile_too_large"


class BlackGlassCapabilityResponse(BaseModel):
    """Contract-level availability without claiming a worker is running."""

    schema_version: Literal["1.0"] = "1.0"
    delivery_endpoints: dict[str, str]
    analyses: list[AnalysisRoute]
    guarantees: list[str]


class BlackGlassAwsImportRequest(BaseModel):
    """One bounded, resumable import step over the configured persons prefix."""

    schema_version: Literal["1.0"] = "1.0"
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=4096)
    dry_run: bool = False


class BlackGlassAwsObjectResult(BaseModel):
    """Outcome for one source object without returning its bytes."""

    key: str
    external_person_id: str | None = None
    etag: str
    size_bytes: int
    status: Literal["eligible", "accepted", "already_enrolled", "skipped", "failed"]
    person_uuid: UUID | None = None
    face_sample_uuid: UUID | None = None
    processing_state: ProcessingState | None = None
    detail: str | None = None


class BlackGlassAwsImportResponse(BaseModel):
    """A resumable page acknowledgement."""

    schema_version: Literal["1.0"] = "1.0"
    bucket: str
    prefix: str
    dry_run: bool
    scanned: int
    accepted: int
    already_enrolled: int
    skipped: int
    failed: int
    next_cursor: str | None
    items: list[BlackGlassAwsObjectResult]


class BlackGlassAwsStatusResponse(BaseModel):
    """Non-secret configuration and connectivity state."""

    configured: Literal[True] = True
    accessible: bool
    bucket: str
    prefix: str
    normalization_pipeline: str
    vector_store: str


class BlackGlassAwsImportError(FaceIdError):
    """The upstream AWS source failed before a page could be processed."""

    status_code = status.HTTP_502_BAD_GATEWAY
    code = "blackglass_aws_unavailable"


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
            "aws_status": "/api/v1/integrations/blackglass/aws/status",
            "aws_face_import": "/api/v1/integrations/blackglass/aws/faces/import",
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


def _external_person_id(key: str, prefix: str) -> str | None:
    """Extract `persons/{personId}/...` without trusting a filename as identity."""
    if not key.startswith(prefix):
        return None
    relative = key[len(prefix) :]
    person_id, separator, filename = relative.partition("/")
    if not separator or not person_id.strip() or not filename.strip():
        return None
    cleaned = person_id.strip()
    return cleaned if len(cleaned) <= 256 else None


@router.get(
    "/aws/status",
    response_model=BlackGlassAwsStatusResponse,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def blackglass_aws_status(
    source: Annotated[BlackGlassS3Source, Depends(get_blackglass_s3_source)],
    _principal: Annotated[Principal, Depends(require(Scope.ADMIN))],
) -> BlackGlassAwsStatusResponse:
    """Verify the optional source without exposing its credentials."""
    accessible = True
    try:
        await source.ping()
    except BlackGlassS3Error:
        accessible = False
    return BlackGlassAwsStatusResponse(
        accessible=accessible,
        bucket=source.config.bucket,
        prefix=source.config.prefix,
        normalization_pipeline="SCRFD detection + aligned 112x112 crop + AdaFace",
        vector_store="Qdrant face embedding collection",
    )


@router.post(
    "/aws/faces/import",
    response_model=BlackGlassAwsImportResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def import_blackglass_aws_faces(
    body: BlackGlassAwsImportRequest,
    source: Annotated[BlackGlassS3Source, Depends(get_blackglass_s3_source)],
    enrolments: Annotated[EnrolmentService, Depends(get_enrolment_service)],
    _principal: Annotated[Principal, Depends(require(Scope.ADMIN))],
) -> BlackGlassAwsImportResponse:
    """Fetch one AWS page and enqueue every valid person image for embedding.

    Expensive face normalization remains in the durable GPU worker. This API
    downloads and validates source bytes, resolves the source-scoped person,
    stores the image, and creates the existing idempotent embedding job.
    """
    try:
        page = await source.list_page(limit=body.limit, cursor=body.cursor)
    except BlackGlassS3Error as exc:
        raise BlackGlassAwsImportError(str(exc)) from exc

    results: list[BlackGlassAwsObjectResult] = []
    for item in page.objects:
        external_id = _external_person_id(item.key, source.config.prefix)
        if external_id is None:
            results.append(
                BlackGlassAwsObjectResult(
                    key=item.key,
                    etag=item.etag,
                    size_bytes=item.size_bytes,
                    status="skipped",
                    detail="key does not match persons/{personId}/{image}",
                )
            )
            continue
        if item.size_bytes <= 0 or item.size_bytes > MAX_IMAGE_BYTES:
            results.append(
                BlackGlassAwsObjectResult(
                    key=item.key,
                    external_person_id=external_id,
                    etag=item.etag,
                    size_bytes=item.size_bytes,
                    status="skipped",
                    detail=f"image must be between 1 and {MAX_IMAGE_BYTES} bytes",
                )
            )
            continue
        if body.dry_run:
            results.append(
                BlackGlassAwsObjectResult(
                    key=item.key,
                    external_person_id=external_id,
                    etag=item.etag,
                    size_bytes=item.size_bytes,
                    status="eligible",
                )
            )
            continue

        try:
            downloaded = await source.read(item.key, max_bytes=MAX_IMAGE_BYTES)
            detected = sniff(downloaded.data, allowed=IMAGE_FORMATS)
            verify_declared(downloaded.content_type, detected.format)
            enrolled = await enrolments.enrol(
                EnrolmentRequest(
                    source=f"blackglass-aws:{source.config.bucket}",
                    image=downloaded.data,
                    external_id=external_id,
                )
            )
        except (
            BlackGlassS3Error,
            DomainValidationError,
            EnrolmentError,
            MediaTooLargeError,
            UnsupportedMediaError,
        ) as exc:
            results.append(
                BlackGlassAwsObjectResult(
                    key=item.key,
                    external_person_id=external_id,
                    etag=item.etag,
                    size_bytes=item.size_bytes,
                    status="failed",
                    detail=str(exc),
                )
            )
            continue
        results.append(
            BlackGlassAwsObjectResult(
                key=item.key,
                external_person_id=external_id,
                etag=item.etag,
                size_bytes=item.size_bytes,
                status="accepted" if enrolled.created else "already_enrolled",
                person_uuid=enrolled.person.person_uuid,
                face_sample_uuid=enrolled.sample.face_sample_uuid,
                processing_state=enrolled.sample.processing_state,
            )
        )

    counts = {
        state: sum(item.status == state for item in results)
        for state in (
            "accepted",
            "already_enrolled",
            "skipped",
            "failed",
        )
    }
    return BlackGlassAwsImportResponse(
        bucket=source.config.bucket,
        prefix=source.config.prefix,
        dry_run=body.dry_run,
        scanned=len(results),
        accepted=counts["accepted"],
        already_enrolled=counts["already_enrolled"],
        skipped=counts["skipped"],
        failed=counts["failed"],
        next_cursor=page.next_cursor,
        items=results,
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
    service: Annotated[MediaService, Depends(get_media_service)],
    principal: Annotated[Principal, Depends(require(Scope.MEDIA_WRITE))],
    source_id: Annotated[str | None, Form(min_length=1, max_length=256)] = None,
    source_type: Annotated[str | None, Form(min_length=1, max_length=64)] = None,
    attributes: Annotated[str, Form(max_length=16000)] = "{}",
    external_object_id: Annotated[str | None, Form(min_length=1, max_length=256)] = None,
    external_object_type: Annotated[str | None, Form(min_length=1, max_length=64)] = None,
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
        resolved_id = source_id or external_object_id
        resolved_type = source_type or external_object_type
        if resolved_id is None or resolved_type is None:
            raise InvalidMediaError("source_id and source_type are required")
        try:
            source_attributes = json.loads(attributes)
        except json.JSONDecodeError as exc:
            raise InvalidMediaError("attributes must be a JSON object", field="attributes") from exc
        if not isinstance(source_attributes, dict):
            raise InvalidMediaError("attributes must be a JSON object", field="attributes")
        source_attributes = {
            **source_attributes,
            "source_system": source_system,
            **({"source_url": source_url} if source_url else {}),
            **({"collected_at": collected_at.isoformat()} if collected_at else {}),
            **({"published_at": published_at.isoformat()} if published_at else {}),
            **({"collector_version": collector_version} if collector_version else {}),
        }
        requested = _requested(requested_analyses)
        data = await read_bounded_upload(file, service.max_bytes)
        result = await service.ingest(
            IngestRequest(
                data=data,
                source_type=SourceType.BLACKGLASS,
                source_system=source_system,
                declared_content_type=file.content_type,
                classification=classification,
                external_source_id=resolved_id,
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

    if not requested:
        requested = _default_analyses(result.asset.mime_type)
    return BlackGlassIngestionResponse(
        source_id=resolved_id,
        source_type=resolved_type,
        attributes=source_attributes,
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
        "source_id": body.source_id,
        "source_type": body.source_type,
        "language_hint": body.language_hint,
    }
    source_system = str(body.attributes.get("source_system", "blackglass-prod"))
    document, created = await service.submit(
        DocumentSubmission(
            title=body.title,
            source=source_system,
            text=body.text,
            attributes=attributes,
        )
    )
    return BlackGlassIngestionResponse(
        source_id=body.source_id,
        source_type=body.source_type,
        attributes=body.attributes,
        subject=IngestedSubject(type="language_document", id=document.document_uuid),
        status="accepted" if created else "already_exists",
        created=created,
        content_type="text/plain; charset=utf-8",
        analysis_routes=[_route(item, "text/plain") for item in body.requested_analyses],
        processing_state=document.processing_state,
    )


@router.get(
    "/documents",
    response_model=BlackGlassDocumentPage,
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}},
)
async def list_blackglass_documents(
    queries: Annotated[ReadQueries, Depends(get_read_queries)],
    _principal: Annotated[Principal, Depends(require(Scope.LANGUAGE))],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    profile_id: Annotated[str | None, Query(max_length=256)] = None,
    processing_state: Annotated[ProcessingState | None, Query()] = None,
) -> BlackGlassDocumentPage:
    """Return imported text records and their current indexing state."""
    page = await queries.list_language_documents(
        limit=limit,
        offset=offset,
        profile_id=profile_id,
        processing_state=processing_state.value if processing_state is not None else None,
    )
    return BlackGlassDocumentPage(
        items=[
            BlackGlassDocumentSummary.model_validate(item, from_attributes=True)
            for item in page.items
        ],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
        profiles=await queries.language_document_profile_counts(),
    )


@router.post(
    "/profiles/{profile_id}/analyze",
    status_code=status.HTTP_202_ACCEPTED,
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}},
)
async def analyze_blackglass_profile(
    profile_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(require(Scope.MEDIA_WRITE))],
) -> dict[str, Any]:
    """Create one cited report run from every imported post for a profile."""
    if not principal.has(Scope.LANGUAGE):
        from app.api.v1.security import NotAuthorisedError

        raise NotAuthorisedError("profile analysis requires the 'language' scope")
    if not profile_id or len(profile_id) > 256:
        raise BlackGlassProfileNotFoundError("profile not found")

    async with _postgres(request).session() as session:
        rows = (
            (
                await session.execute(
                    select(
                        language_documents.c.source_id,
                        language_documents.c.title,
                        language_documents.c.original_text,
                        language_documents.c.created_at,
                    )
                    .where(language_documents.c.profile_id == profile_id)
                    .order_by(language_documents.c.created_at, language_documents.c.source_id)
                )
            )
            .mappings()
            .all()
        )
        if not rows:
            raise BlackGlassProfileNotFoundError("profile not found")

        blocks = []
        for row in rows:
            blocks.append(
                "\n".join(
                    [
                        f"[SOURCE {row['source_id']}]",
                        row["original_text"],
                        "[/SOURCE]",
                    ]
                )
            )
        corpus = "\n\n".join(blocks)
        if len(corpus) > 100_000:
            raise BlackGlassProfileTooLargeError(
                "profile text exceeds 100,000 characters; submit bounded report batches"
            )

        label = str(rows[0]["title"]).split(" — ", 1)[0][:512]
        submission = TextSubmission(
            source_id=profile_id,
            source_type="profile_post_collection",
            report_request_id=profile_id,
            subject=ReportSubject(
                subject_type="social_profile",
                subject_id=profile_id,
                display_label=label,
            ),
            attributes={
                "source_system": "blackglass-prod",
                "profile_id": profile_id,
                "record_count": len(rows),
                "report_profile": "full-intelligence-v1",
            },
            options=AnalysisOptions(language="mixed", summarize=True),
            text=corpus,
        )
        return await EvidenceRepository(session).submit(
            principal.subject,
            submission,
            sha256=content_hash(corpus.encode()),
            text=corpus,
        )
