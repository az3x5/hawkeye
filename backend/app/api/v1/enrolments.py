"""Enrolment endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile, status
from pydantic import BaseModel, Field

from app.api.v1.dependencies import (
    get_enrolment_service,
    get_object_store,
    get_sample_reader,
)
from app.api.v1.security import require
from app.connectors.filesystem import FilesystemObjectStore
from app.core.errors import ErrorResponse, FaceIdError
from app.domain.auth import Principal, Scope
from app.domain.jobs import ProcessingState
from app.domain.models import DomainValidationError
from app.services.enrolment import (
    EnrolmentError,
    EnrolmentRequest,
    EnrolmentService,
    SampleReader,
)

router = APIRouter(tags=["enrolment"])

#: Upload ceiling. Larger images are rejected before anything is read into
#: memory-resident storage.
MAX_IMAGE_BYTES = 10 * 1024 * 1024

ALLOWED_CONTENT_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})


class InvalidEnrolmentError(FaceIdError):
    """The enrolment request was well-formed but cannot be accepted."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "invalid_enrolment"


class ConflictingIdentifiersError(FaceIdError):
    """The supplied identifiers already denote different people."""

    status_code = status.HTTP_409_CONFLICT
    code = "conflicting_identifiers"


class SampleNotFoundError(FaceIdError):
    """No such face sample."""

    status_code = status.HTTP_404_NOT_FOUND
    code = "face_sample_not_found"


class FaceSampleResponse(BaseModel):
    """State of one enrolled face sample."""

    face_sample_uuid: UUID = Field(description="Internal identifier of this sample.")
    person_uuid: UUID = Field(description="Internal identifier of the person.")
    source: str = Field(description="System the sample was submitted from.")
    image_sha256: str = Field(description="Content hash of the submitted image.")
    processing_state: ProcessingState = Field(
        description="Whether the sample has been embedded yet."
    )
    captured_at: datetime | None = Field(default=None, description="When the image was taken.")
    created_at: datetime = Field(description="When the sample was enrolled.")
    processed_at: datetime | None = Field(
        default=None, description="When processing finished, successfully or not."
    )
    failure_reason: str | None = Field(
        default=None, description="Why processing failed, when it did."
    )


class EnrolmentResponse(BaseModel):
    """Outcome of an enrolment submission."""

    person_uuid: UUID = Field(description="Internal identifier of the person.")
    sample: FaceSampleResponse
    created: bool = Field(
        description=(
            "False when this submission repeated an existing one. Repeats are "
            "not errors: they return the same identifiers and schedule no "
            "further work."
        )
    )
    status: Literal["accepted", "already_enrolled"] = Field(
        description="Whether new work was scheduled."
    )


def _to_response(person_uuid: UUID, sample: object, created: bool) -> EnrolmentResponse:
    return EnrolmentResponse(
        person_uuid=person_uuid,
        sample=FaceSampleResponse.model_validate(sample, from_attributes=True),
        created=created,
        status="accepted" if created else "already_enrolled",
    )


async def read_image_upload(upload: UploadFile) -> bytes:
    """Validate an uploaded image and return its bytes."""
    if upload.content_type not in ALLOWED_CONTENT_TYPES:
        raise InvalidEnrolmentError(
            f"unsupported image type {upload.content_type!r}; "
            f"expected one of {', '.join(sorted(ALLOWED_CONTENT_TYPES))}",
            field="image",
        )
    data = await upload.read()
    if not data:
        raise InvalidEnrolmentError("the uploaded image is empty", field="image")
    if len(data) > MAX_IMAGE_BYTES:
        raise InvalidEnrolmentError(
            f"image is {len(data)} bytes, which exceeds the {MAX_IMAGE_BYTES} byte limit",
            field="image",
        )
    return data


@router.post(
    "/enrolments",
    response_model=EnrolmentResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Enrol a face sample",
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def create_enrolment(
    source: Annotated[str, Form(min_length=1, max_length=128)],
    image: Annotated[UploadFile, File()],
    service: Annotated[EnrolmentService, Depends(get_enrolment_service)],
    _principal: Annotated[Principal, Depends(require(Scope.ENROL))],
    external_id: Annotated[str | None, Form(max_length=256)] = None,
    local_id: Annotated[str | None, Form(max_length=256)] = None,
    captured_at: Annotated[datetime | None, Form()] = None,
) -> EnrolmentResponse:
    """Record a face sample for a person and schedule it for embedding.

    Idempotent: resubmitting the same image under the same identifiers returns
    the original identifiers and schedules no further work.
    """
    data = await read_image_upload(image)
    try:
        enrolment = EnrolmentRequest(
            source=source,
            image=data,
            external_id=external_id,
            local_id=local_id,
            captured_at=captured_at,
        )
        result = await service.enrol(enrolment)
    except DomainValidationError as exc:
        raise InvalidEnrolmentError(str(exc)) from exc
    except EnrolmentError as exc:
        raise ConflictingIdentifiersError(str(exc)) from exc

    return _to_response(result.person.person_uuid, result.sample, result.created)


@router.get(
    "/face-samples/{face_sample_uuid}/image",
    summary="Fetch an enrolled face sample image",
    response_class=Response,
    responses={
        200: {"content": {"image/jpeg": {}}},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def read_face_sample_image(
    face_sample_uuid: UUID,
    reader: Annotated[SampleReader, Depends(get_sample_reader)],
    objects: Annotated[FilesystemObjectStore, Depends(get_object_store)],
    _principal: Annotated[Principal, Depends(require(Scope.REVIEW))],
) -> Response:
    """Return an enrolled image so a reviewer can compare it with a query.

    Served only for a known sample, never by raw content hash.
    """
    sample = await reader.get(face_sample_uuid)
    if sample is None:
        raise SampleNotFoundError(f"no face sample {face_sample_uuid}")
    data = await objects.get(sample.image_sha256)
    if data is None:
        raise SampleNotFoundError(f"the image for {face_sample_uuid} is no longer stored")
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, no-store"},
    )


@router.get(
    "/face-samples/{face_sample_uuid}",
    response_model=FaceSampleResponse,
    summary="Read the state of a face sample",
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def read_face_sample(
    face_sample_uuid: UUID,
    reader: Annotated[SampleReader, Depends(get_sample_reader)],
    _principal: Annotated[Principal, Depends(require(Scope.ENROL))],
) -> FaceSampleResponse:
    """Return one face sample, including whether it has been embedded yet."""
    sample = await reader.get(face_sample_uuid)
    if sample is None:
        raise SampleNotFoundError(f"no face sample {face_sample_uuid}")
    return FaceSampleResponse.model_validate(sample, from_attributes=True)
