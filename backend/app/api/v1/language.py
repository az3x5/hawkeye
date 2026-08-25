"""Authenticated Dhivehi language normalization and transliteration APIs."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.v1.security import require
from app.core.errors import ErrorResponse
from app.domain.auth import Principal, Scope
from app.domain.language import PrimaryScript, Script, TransliterationDirection
from app.services.language import NORMALIZER_VERSION, normalize_text, transliterate

router = APIRouter(prefix="/nlp", tags=["language"])

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
            ScriptSpanResponse.model_validate(span, from_attributes=True)
            for span in result.spans
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
