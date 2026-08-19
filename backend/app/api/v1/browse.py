"""Read endpoints for browsing what the system holds.

Everything here is read-only and paginated. They exist because a record that
cannot be read back is of limited use to the people responsible for it — and
because a UI that cannot read them has nothing honest to show.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field

from app.api.v1.dependencies import get_read_queries
from app.api.v1.security import require
from app.connectors.postgres.queries import ReadQueries
from app.core.errors import ErrorResponse, FaceIdError
from app.domain.auth import Principal, Scope

router = APIRouter(tags=["browse"])

_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
    503: {"model": ErrorResponse},
}


class PersonNotFoundError(FaceIdError):
    """No such person."""

    status_code = status.HTTP_404_NOT_FOUND
    code = "person_not_found"


class ExternalIdentifierResponse(BaseModel):
    """An upstream identifier, scoped by its source."""

    source: str
    kind: str
    value: str


class PersonResponse(BaseModel):
    """A person, with enough context to recognise them in a list."""

    person_uuid: UUID
    created_at: datetime
    sample_count: int = Field(description="Face samples enrolled for this person.")
    processed_count: int = Field(description="How many of those have been embedded.")
    identifiers: list[ExternalIdentifierResponse]


class FaceSampleResponse(BaseModel):
    """One enrolled sample."""

    face_sample_uuid: UUID
    person_uuid: UUID
    source: str
    image_sha256: str
    processing_state: str
    captured_at: datetime | None = None
    created_at: datetime
    processed_at: datetime | None = None
    failure_reason: str | None = None


class PersonDetailResponse(PersonResponse):
    """A person and their samples."""

    samples: list[FaceSampleResponse]


class PersonPage(BaseModel):
    """A page of people."""

    items: list[PersonResponse]
    total: int
    limit: int
    offset: int


@router.get(
    "/persons",
    response_model=PersonPage,
    summary="List people",
    responses=_RESPONSES,
)
async def list_persons(
    queries: Annotated[ReadQueries, Depends(get_read_queries)],
    _principal: Annotated[Principal, Depends(require(Scope.REVIEW))],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    search: Annotated[str | None, Query(max_length=256)] = None,
) -> PersonPage:
    """People known to the system, newest first.

    ``search`` matches a person's uuid or any of their external identifiers,
    since those are the two ways anyone refers to someone.
    """
    page = await queries.list_persons(limit=limit, offset=offset, search=search)
    return PersonPage(
        items=[PersonResponse.model_validate(item, from_attributes=True) for item in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get(
    "/persons/{person_uuid}",
    response_model=PersonDetailResponse,
    summary="Read one person",
    responses={**_RESPONSES, 404: {"model": ErrorResponse}},
)
async def read_person(
    person_uuid: UUID,
    queries: Annotated[ReadQueries, Depends(get_read_queries)],
    _principal: Annotated[Principal, Depends(require(Scope.REVIEW))],
) -> PersonDetailResponse:
    """One person, their external identifiers and their samples."""
    person = await queries.get_person(person_uuid)
    if person is None:
        raise PersonNotFoundError(f"no person {person_uuid}")
    samples = await queries.samples_for_person(person_uuid)
    return PersonDetailResponse(
        person_uuid=person.person_uuid,
        created_at=person.created_at,
        sample_count=person.sample_count,
        processed_count=person.processed_count,
        identifiers=[ExternalIdentifierResponse(**i) for i in person.identifiers],
        samples=[FaceSampleResponse(**sample) for sample in samples],
    )


class IdentificationRecord(BaseModel):
    """An identification as recorded, for history rather than review."""

    identification_uuid: UUID
    outcome: str
    policy_version: str
    accept_at: float
    review_at: float
    best_person_uuid: UUID | None = None
    best_score: float | None = None
    created_at: datetime
    review_outcome: str | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    review_note: str | None = None
    candidates: list[dict[str, Any]] = Field(default_factory=list)


class IdentificationPage(BaseModel):
    """A page of identifications."""

    items: list[IdentificationRecord]
    total: int
    limit: int
    offset: int


@router.get(
    "/identification-history",
    response_model=IdentificationPage,
    summary="List identifications, decided or not",
    responses=_RESPONSES,
)
async def list_identification_history(
    queries: Annotated[ReadQueries, Depends(get_read_queries)],
    _principal: Annotated[Principal, Depends(require(Scope.REVIEW))],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    outcome: Annotated[Literal["accept", "review", "reject"] | None, Query()] = None,
    reviewed: Annotated[bool | None, Query()] = None,
    person_uuid: Annotated[UUID | None, Query()] = None,
) -> IdentificationPage:
    """Every identification, most recent first.

    Separate from `/identifications`, which returns only proposals still
    awaiting a human: this is the record of what was decided.
    """
    page = await queries.list_identifications(
        limit=limit, offset=offset, outcome=outcome, reviewed=reviewed, person_uuid=person_uuid
    )
    return IdentificationPage(
        items=[IdentificationRecord(**item) for item in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


class AuditEventResponse(BaseModel):
    """One recorded action."""

    audit_uuid: UUID
    occurred_at: datetime
    action: str
    actor_identifier: str
    actor_kind: str
    person_uuid: UUID | None = None
    face_sample_uuid: UUID | None = None
    identification_uuid: UUID | None = None
    policy_version: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class AuditPage(BaseModel):
    """A page of audit events, and the actions available to filter by."""

    items: list[AuditEventResponse]
    total: int
    limit: int
    offset: int
    actions: list[str]


@router.get(
    "/audit-events",
    response_model=AuditPage,
    summary="Read the audit log",
    responses=_RESPONSES,
)
async def list_audit_events(
    queries: Annotated[ReadQueries, Depends(get_read_queries)],
    _principal: Annotated[Principal, Depends(require(Scope.ADMIN))],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    action: Annotated[str | None, Query(max_length=64)] = None,
    actor: Annotated[str | None, Query(max_length=256)] = None,
    person_uuid: Annotated[UUID | None, Query()] = None,
) -> AuditPage:
    """The append-only log, most recent first.

    Read-only by construction: there is no endpoint that edits or removes an
    audit event, because a record that can be altered afterwards is not
    evidence.
    """
    page = await queries.list_audit_events(
        limit=limit, offset=offset, action=action, actor=actor, person_uuid=person_uuid
    )
    return AuditPage(
        items=[
            AuditEventResponse.model_validate(item, from_attributes=True) for item in page.items
        ],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
        actions=await queries.audit_actions(),
    )


class StatisticsResponse(BaseModel):
    """Counts describing what the system holds. Every figure is a real query."""

    persons: int
    face_samples: int
    samples_by_state: dict[str, int]
    embeddings: int
    identifications: int
    identifications_by_outcome: dict[str, int]
    awaiting_review: int
    reviews_recorded: int
    audit_events: int


@router.get(
    "/statistics",
    response_model=StatisticsResponse,
    summary="Aggregate counts",
    responses=_RESPONSES,
)
async def read_statistics(
    queries: Annotated[ReadQueries, Depends(get_read_queries)],
    _principal: Annotated[Principal, Depends(require(Scope.REVIEW))],
) -> StatisticsResponse:
    """Totals across the metadata store.

    Counts only: no rates, trends or derived indicators, because those would
    need a time series the system does not keep.
    """
    stats = await queries.statistics()
    return StatisticsResponse.model_validate(stats, from_attributes=True)
