"""Authenticated Dhivehi language normalization and transliteration APIs."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, UploadFile, status
from pydantic import BaseModel, Field, model_validator

from app.api.v1.dependencies import (
    get_dhivehi_ai_client,
    get_language_document_service,
    get_language_search_service,
)
from app.api.v1.security import require
from app.core.errors import ErrorResponse, FaceIdError
from app.domain.auth import Principal, Scope
from app.domain.jobs import ProcessingState
from app.domain.language import PrimaryScript, Script, TransliterationDirection
from app.services.dhivehi_ai_client import DhivehiAIClient, DhivehiAIServiceError
from app.services.dhivehi_models import BotMessage, DhivehiTask
from app.services.language import NORMALIZER_VERSION, normalize_text, transliterate
from app.services.language_search import (
    DocumentSubmission,
    LanguageDocumentService,
    LanguageSearchService,
)

router = APIRouter(prefix="/nlp", tags=["language"])


class LanguageDocumentNotFoundError(FaceIdError):
    """No language document exists under the supplied identifier."""

    status_code = status.HTTP_404_NOT_FOUND
    code = "language_document_not_found"


class DhivehiInferenceUnavailableError(FaceIdError):
    """Specialist inference is not able to execute the requested model."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "dhivehi_inference_unavailable"


class LanguageUploadTooLargeError(FaceIdError):
    """A speech or OCR upload exceeded its bounded request limit."""

    status_code = status.HTTP_413_CONTENT_TOO_LARGE
    code = "language_upload_too_large"


_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}


class TextRequest(BaseModel):
    """Bounded text accepted by synchronous language operations."""

    text: str = Field(min_length=1, max_length=20_000)


class ScriptSpanResponse(BaseModel):
    """One script-classified span using Python string offsets."""

    text: str
    start: int
    end: int
    script: Script


class NormalizationResponse(BaseModel):
    """Canonical text and observable script composition."""

    original: str
    normalized: str
    primary_script: PrimaryScript
    spans: list[ScriptSpanResponse]
    normalizer_version: str


class TransliterationRequest(TextRequest):
    """Text plus an explicit direction; no language guess is hidden."""

    direction: TransliterationDirection


class TransliterationResponse(BaseModel):
    """Versioned transliteration output and any quality caveats."""

    original: str
    output: str
    direction: TransliterationDirection
    model_version: str
    warnings: list[str]


class NeuralTextRequest(TextRequest):
    """Text submitted to one explicit neural language operation."""

    task: Literal[
        DhivehiTask.LATIN_TO_THAANA,
        DhivehiTask.THAANA_TO_LATIN,
        DhivehiTask.DHIVEHI_TO_ENGLISH,
        DhivehiTask.ENGLISH_TO_DHIVEHI,
    ]


class NeuralInferenceResponse(BaseModel):
    """Model output with exact provenance and an accuracy caveat."""

    task: str
    text: str
    model: str
    model_revision: str
    quality_summary: str
    limitation: str


class BotMessageRequest(BaseModel):
    """One untrusted conversation turn accepted by the authenticated API."""

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4_000)


class BotRequest(BaseModel):
    """A bounded conversational request with an explicit response language."""

    messages: list[BotMessageRequest] = Field(min_length=1, max_length=20)
    response_language: Literal["auto", "dhivehi", "english"] = "auto"

    @model_validator(mode="after")
    def validate_conversation(self) -> BotRequest:
        """Prevent unbounded prompt growth and assistant-only generation."""
        if self.messages[-1].role != "user":
            raise ValueError("the final chat message must have role user")
        if sum(len(message.content) for message in self.messages) > 20_000:
            raise ValueError("chat context exceeds 20,000 characters")
        return self


class ModelCapabilityResponse(BaseModel):
    """Truthful availability and evaluation metadata for one task."""

    task: str
    model_id: str
    revision: str
    license: str
    runtime: str
    status: str
    quality_summary: str
    limitation: str
    loaded: bool


class ModelCapabilitiesResponse(BaseModel):
    """All specialist models known to the deployment."""

    capabilities: list[ModelCapabilityResponse]


class DocumentRequest(BaseModel):
    """One text document submitted for asynchronous semantic indexing."""

    title: str = Field(min_length=1, max_length=256)
    source: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=100_000)
    attributes: dict[str, Any] = Field(default_factory=dict)


class DocumentResponse(BaseModel):
    """Persisted document state and embedding provenance."""

    document_uuid: UUID
    title: str
    source: str
    original_text: str
    normalized_text: str
    primary_script: PrimaryScript
    content_sha256: str
    attributes: dict[str, Any]
    processing_state: ProcessingState
    embedding_model: str | None
    embedding_version: str | None
    vector_collection: str | None
    failure_reason: str | None
    created_at: datetime
    processed_at: datetime | None
    created: bool | None = None
    status: Literal["accepted", "already_exists"] | None = None


class SearchRequest(TextRequest):
    """Semantic query and optional source/similarity filters."""

    limit: int = Field(default=10, ge=1, le=50)
    source: str | None = Field(default=None, min_length=1, max_length=128)
    minimum_score: float | None = Field(default=None, ge=-1.0, le=1.0)


class SearchHitResponse(BaseModel):
    """One similarity-ranked language document."""

    document_uuid: UUID
    title: str
    source: str
    text: str
    primary_script: PrimaryScript
    score: float
    attributes: dict[str, Any]


class SearchResponse(BaseModel):
    """Semantic search results and exact embedding provenance."""

    query: str
    model: str
    model_version: str
    hits: list[SearchHitResponse]


def _document_response(
    document: object,
    *,
    created: bool | None = None,
) -> DocumentResponse:
    response = DocumentResponse.model_validate(document, from_attributes=True)
    if created is None:
        return response
    return response.model_copy(
        update={
            "created": created,
            "status": "accepted" if created else "already_exists",
        }
    )


@router.post(
    "/normalize",
    response_model=NormalizationResponse,
    responses=_RESPONSES,
    summary="Normalize and classify Dhivehi, Latin and mixed text",
)
async def normalize(
    body: TextRequest,
    _principal: Annotated[Principal, Depends(require(Scope.LANGUAGE))],
) -> NormalizationResponse:
    """Return canonical Unicode without discarding the submitted original."""
    result = normalize_text(body.text)
    return NormalizationResponse(
        original=result.original,
        normalized=result.normalized,
        primary_script=result.primary_script,
        spans=[
            ScriptSpanResponse.model_validate(span, from_attributes=True) for span in result.spans
        ],
        normalizer_version=NORMALIZER_VERSION,
    )


@router.post(
    "/transliterate",
    response_model=TransliterationResponse,
    responses=_RESPONSES,
    summary="Transliterate explicitly between Latin Dhivehi and Thaana",
)
async def transliterate_text(
    body: TransliterationRequest,
    _principal: Annotated[Principal, Depends(require(Scope.LANGUAGE))],
) -> TransliterationResponse:
    """Return a versioned rule result; Latin ambiguity is surfaced."""
    result = transliterate(body.text, body.direction)
    return TransliterationResponse(
        original=result.original,
        output=result.output,
        direction=result.direction,
        model_version=result.model_version,
        warnings=list(result.warnings),
    )


@router.get(
    "/models",
    response_model=ModelCapabilitiesResponse,
    responses=_RESPONSES | {503: {"model": ErrorResponse}},
    summary="List installed, available and blocked Dhivehi models",
)
async def language_models(
    client: Annotated[DhivehiAIClient, Depends(get_dhivehi_ai_client)],
    _principal: Annotated[Principal, Depends(require(Scope.LANGUAGE))],
) -> ModelCapabilitiesResponse:
    """Expose actual deployment state rather than a static feature claim."""
    try:
        capabilities = await client.capabilities()
    except DhivehiAIServiceError as exc:
        raise DhivehiInferenceUnavailableError(str(exc)) from exc
    return ModelCapabilitiesResponse(
        capabilities=[ModelCapabilityResponse.model_validate(item) for item in capabilities]
    )


@router.post(
    "/infer",
    response_model=NeuralInferenceResponse,
    responses=_RESPONSES | {503: {"model": ErrorResponse}},
    summary="Translate or neurally transliterate Dhivehi text",
)
async def infer_text(
    body: NeuralTextRequest,
    client: Annotated[DhivehiAIClient, Depends(get_dhivehi_ai_client)],
    _principal: Annotated[Principal, Depends(require(Scope.LANGUAGE))],
) -> NeuralInferenceResponse:
    """Run a pinned specialist model with explicit direction and provenance."""
    try:
        result = await client.text(DhivehiTask(body.task), body.text)
    except DhivehiAIServiceError as exc:
        raise DhivehiInferenceUnavailableError(str(exc)) from exc
    return NeuralInferenceResponse.model_validate(result)


@router.post(
    "/chat",
    response_model=NeuralInferenceResponse,
    responses=_RESPONSES | {503: {"model": ErrorResponse}},
    summary="Chat with the private local Dhivehi intelligence assistant",
)
async def chat(
    body: BotRequest,
    client: Annotated[DhivehiAIClient, Depends(get_dhivehi_ai_client)],
    _principal: Annotated[Principal, Depends(require(Scope.LANGUAGE))],
) -> NeuralInferenceResponse:
    """Forward a bounded conversation while retaining the server-owned system prompt."""
    messages: list[BotMessage] = [
        {"role": message.role, "content": message.content} for message in body.messages
    ]
    try:
        result = await client.chat(messages, body.response_language)
    except DhivehiAIServiceError as exc:
        raise DhivehiInferenceUnavailableError(str(exc)) from exc
    return NeuralInferenceResponse.model_validate(result)


async def _bounded_upload(upload: UploadFile, limit: int) -> bytes:
    """Read an upload incrementally and reject it before unbounded buffering."""
    chunks: list[bytes] = []
    total = 0
    while chunk := await upload.read(min(1024 * 1024, limit - total + 1)):
        total += len(chunk)
        if total > limit:
            raise LanguageUploadTooLargeError(f"upload exceeds {limit} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post(
    "/speech/transcribe",
    response_model=NeuralInferenceResponse,
    responses=_RESPONSES | {503: {"model": ErrorResponse}},
    summary="Transcribe Dhivehi speech into Thaana",
)
async def transcribe_speech(
    client: Annotated[DhivehiAIClient, Depends(get_dhivehi_ai_client)],
    _principal: Annotated[Principal, Depends(require(Scope.LANGUAGE))],
    file: Annotated[UploadFile, File(description="WAV, FLAC or OGG Dhivehi speech")],
) -> NeuralInferenceResponse:
    """Forward bounded audio to the isolated Dhivehi ASR model."""
    content = await _bounded_upload(file, 50 * 1024 * 1024)
    try:
        result = await client.speech(content, file.content_type or "application/octet-stream")
    except DhivehiAIServiceError as exc:
        raise DhivehiInferenceUnavailableError(str(exc)) from exc
    return NeuralInferenceResponse.model_validate(result)


@router.post(
    "/ocr",
    response_model=NeuralInferenceResponse,
    responses=_RESPONSES | {503: {"model": ErrorResponse}},
    summary="Recognize Thaana text in an image crop",
)
async def recognize_thaana(
    client: Annotated[DhivehiAIClient, Depends(get_dhivehi_ai_client)],
    _principal: Annotated[Principal, Depends(require(Scope.LANGUAGE))],
    file: Annotated[UploadFile, File(description="Image containing Thaana text")],
) -> NeuralInferenceResponse:
    """Forward a bounded text crop to the isolated Dhivehi OCR model."""
    content = await _bounded_upload(file, 25 * 1024 * 1024)
    try:
        result = await client.ocr(content, file.content_type or "application/octet-stream")
    except DhivehiAIServiceError as exc:
        raise DhivehiInferenceUnavailableError(str(exc)) from exc
    return NeuralInferenceResponse.model_validate(result)


@router.post(
    "/documents",
    response_model=DocumentResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses=_RESPONSES | {503: {"model": ErrorResponse}},
    summary="Store and schedule a language document for semantic indexing",
)
async def create_document(
    body: DocumentRequest,
    service: Annotated[LanguageDocumentService, Depends(get_language_document_service)],
    _principal: Annotated[Principal, Depends(require(Scope.LANGUAGE))],
) -> DocumentResponse:
    """Persist before enqueueing; repeated source/content submissions converge."""
    document, created = await service.submit(
        DocumentSubmission(
            title=body.title,
            source=body.source,
            text=body.text,
            attributes=body.attributes,
        )
    )
    return _document_response(document, created=created)


@router.get(
    "/documents/{document_uuid}",
    response_model=DocumentResponse,
    responses=_RESPONSES | {404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
    summary="Read language document processing state",
)
async def read_document(
    document_uuid: UUID,
    service: Annotated[LanguageDocumentService, Depends(get_language_document_service)],
    _principal: Annotated[Principal, Depends(require(Scope.LANGUAGE))],
) -> DocumentResponse:
    """Return processing state so clients can poll an asynchronous submission."""
    document = await service.get(document_uuid)
    if document is None:
        raise LanguageDocumentNotFoundError(f"no language document {document_uuid}")
    return _document_response(document)


@router.post(
    "/search",
    response_model=SearchResponse,
    responses=_RESPONSES | {503: {"model": ErrorResponse}},
    summary="Search indexed text by multilingual semantic similarity",
)
async def semantic_search(
    body: SearchRequest,
    service: Annotated[LanguageSearchService, Depends(get_language_search_service)],
    _principal: Annotated[Principal, Depends(require(Scope.LANGUAGE))],
) -> SearchResponse:
    """Embed a query and return hydrated similarity-ranked documents."""
    hits, model, version = await service.search(
        body.text,
        limit=body.limit,
        source=body.source,
        minimum_score=body.minimum_score,
    )
    return SearchResponse(
        query=body.text,
        model=model,
        model_version=version,
        hits=[SearchHitResponse.model_validate(hit, from_attributes=True) for hit in hits],
    )
