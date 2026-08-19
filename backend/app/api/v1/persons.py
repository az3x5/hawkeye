"""Person administration."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field

from app.api.v1.dependencies import get_person_eraser
from app.api.v1.security import require
from app.core.errors import ErrorResponse, FaceIdError
from app.domain.auth import Principal, Scope
from app.services.erasure import PersonEraser, PersonNotFoundError

router = APIRouter(tags=["administration"])


class PersonNotFoundHttpError(FaceIdError):
    """No such person."""

    status_code = status.HTTP_404_NOT_FOUND
    code = "person_not_found"


class ErasureResponse(BaseModel):
    """What an erasure removed."""

    person_uuid: UUID
    samples_removed: int = Field(description="Face samples deleted with the person.")
    vectors_removed: int = Field(
        description="Embeddings removed, counted across every model provenance."
    )
    images_removed: int = Field(
        description=(
            "Stored images deleted. Lower than samples_removed when another "
            "person was enrolled from the same photograph."
        )
    )


@router.delete(
    "/persons/{person_uuid}",
    response_model=ErasureResponse,
    summary="Erase a person and their biometric material",
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def erase_person(
    person_uuid: UUID,
    eraser: Annotated[PersonEraser, Depends(get_person_eraser)],
    principal: Annotated[Principal, Depends(require(Scope.ADMIN))],
    reason: Annotated[str | None, Query(max_length=1000)] = None,
) -> ErasureResponse:
    """Remove a person's metadata, embeddings and stored images.

    Irreversible, and audited: the audit record names who did it and outlives
    the person, because a deletion that leaves no trace cannot be shown to have
    happened.
    """
    try:
        report = await eraser.erase(person_uuid, actor=principal.as_actor(), reason=reason)
    except PersonNotFoundError as exc:
        raise PersonNotFoundHttpError(str(exc)) from exc

    return ErasureResponse(
        person_uuid=report.person_uuid,
        samples_removed=report.samples_removed,
        vectors_removed=report.vectors_removed,
        images_removed=report.images_removed,
    )
