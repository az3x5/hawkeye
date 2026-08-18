"""Identification and review endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from pydantic import BaseModel, Field

from app.api.v1.dependencies import get_identification_service, get_identification_store
from app.api.v1.enrolments import read_image_upload
from app.core.errors import ErrorResponse, FaceIdError
from app.domain.audit import Actor
from app.domain.identity import (
    Candidate,
    DecisionOutcome,
    IdentificationStore,
    IdentityDecision,
    ReviewOutcome,
)
from app.services.identification import IdentificationError, IdentificationService

router = APIRouter(tags=["identification"])


class IdentificationFailedError(FaceIdError):
    """The query image could not be turned into a decision."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "identification_failed"


class IdentificationNotFoundError(FaceIdError):
    """No such identification."""

    status_code = status.HTTP_404_NOT_FOUND
    code = "identification_not_found"


class ReviewNotPermittedError(FaceIdError):
    """This identification cannot be reviewed."""

    status_code = status.HTTP_409_CONFLICT
    code = "review_not_permitted"


class CandidateResponse(BaseModel):
    """One person who might be the subject of the query."""

    person_uuid: UUID
    face_sample_uuid: UUID = Field(description="The person's best-matching sample.")
    score: float = Field(
        ge=-1.0,
        le=1.0,
        description=(
            "Cosine similarity to the best-matching sample. This is a raw, "
            "uncalibrated similarity — it is not a probability and must not be "
            "presented as one."
        ),
    )
    sample_count: int = Field(description="How many of this person's samples were seen.")


class ThresholdsResponse(BaseModel):
    """The policy that produced a decision."""

    accept_at: float
    review_at: float
    policy_version: str


class IdentificationResponse(BaseModel):
    """A proposal about who a face belongs to."""

    identification_uuid: UUID
    outcome: DecisionOutcome = Field(
        description=(
            "'accept' proposes a match, 'review' asks for a human, 'reject' "
            "proposes nobody. A proposal is not a determination of identity."
        )
    )
    thresholds: ThresholdsResponse
    candidates: list[CandidateResponse]
    margin: float | None = Field(
        default=None,
        description="Gap between the top two candidates; null when fewer than two.",
    )
    review_outcome: ReviewOutcome | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    review_note: str | None = None


class ReviewRequest(BaseModel):
    """A human's conclusion about an identification."""

    outcome: ReviewOutcome = Field(description="What the reviewer concluded.")
    reviewer: str = Field(
        min_length=1, max_length=256, description="Who reviewed it. Recorded in the audit log."
    )
    note: str | None = Field(
        default=None, max_length=2000, description="Why, in the reviewer's words."
    )


def _decision_response(
    identification_uuid: UUID, decision: IdentityDecision, **review: object
) -> IdentificationResponse:
    return IdentificationResponse(
        identification_uuid=identification_uuid,
        outcome=decision.outcome,
        thresholds=ThresholdsResponse(
            accept_at=decision.thresholds.accept_at,
            review_at=decision.thresholds.review_at,
            policy_version=decision.thresholds.policy_version,
        ),
        candidates=[_candidate(c) for c in decision.candidates],
        margin=decision.margin,
        **review,  # type: ignore[arg-type]
    )


def _candidate(candidate: Candidate) -> CandidateResponse:
    return CandidateResponse(
        person_uuid=candidate.person_uuid,
        face_sample_uuid=candidate.face_sample_uuid,
        score=candidate.score,
        sample_count=candidate.sample_count,
    )


@router.post(
    "/identifications",
    response_model=IdentificationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Identify a face",
    responses={422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def create_identification(
    image: Annotated[UploadFile, File()],
    service: Annotated[IdentificationService, Depends(get_identification_service)],
    actor: Annotated[str | None, Form(max_length=256)] = None,
) -> IdentificationResponse:
    """Propose who a face belongs to, and record the attempt.

    Returns a proposal with raw similarity scores and the thresholds that
    produced it. It never returns a probability, and 'accept' is a proposal to
    act, not a determination of identity.
    """
    data = await read_image_upload(image)
    try:
        result = await service.identify(
            await service.embed_query(data),
            query_bytes=data,
            actor=Actor(identifier=actor) if actor else None,
        )
    except IdentificationError as exc:
        raise IdentificationFailedError(str(exc)) from exc
    return _decision_response(result.identification_uuid, result.decision)


@router.get(
    "/identifications/{identification_uuid}",
    response_model=IdentificationResponse,
    summary="Read a recorded identification",
    responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def read_identification(
    identification_uuid: UUID,
    store: Annotated[IdentificationStore, Depends(get_identification_store)],
) -> IdentificationResponse:
    """Return a past identification with the policy that produced it."""
    stored = await store.get(identification_uuid)
    if stored is None:
        raise IdentificationNotFoundError(f"no identification {identification_uuid}")
    return _decision_response(
        stored.identification_uuid,
        stored.decision,
        review_outcome=stored.review_outcome,
        reviewed_by=stored.reviewed_by,
        reviewed_at=stored.reviewed_at,
        review_note=stored.review_note,
    )


@router.post(
    "/identifications/{identification_uuid}/review",
    response_model=IdentificationResponse,
    summary="Record a review decision",
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def review_identification(
    identification_uuid: UUID,
    body: ReviewRequest,
    service: Annotated[IdentificationService, Depends(get_identification_service)],
) -> IdentificationResponse:
    """Attach a human's conclusion to an identification that asked for one.

    Only proposals with outcome 'review' can be reviewed, and only once.
    """
    try:
        stored = await service.review(
            identification_uuid,
            outcome=body.outcome,
            reviewer=Actor(identifier=body.reviewer),
            note=body.note,
        )
    except IdentificationError as exc:
        message = str(exc)
        if "no identification" in message:
            raise IdentificationNotFoundError(message) from exc
        raise ReviewNotPermittedError(message) from exc

    return _decision_response(
        stored.identification_uuid,
        stored.decision,
        review_outcome=stored.review_outcome,
        reviewed_by=stored.reviewed_by,
        reviewed_at=stored.reviewed_at,
        review_note=stored.review_note,
    )
