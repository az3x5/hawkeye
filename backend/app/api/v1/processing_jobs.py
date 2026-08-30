"""Administrative visibility and control for durable processing jobs."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field

from app.api.v1.dependencies import get_processing_job_administration
from app.api.v1.security import require
from app.core.errors import ErrorResponse, FaceIdError
from app.domain.auth import Principal, Scope
from app.domain.processing import JobStateError, JobStatus, ProcessingJob
from app.services.processing import ProcessingJobAdministration

router = APIRouter(prefix="/processing/jobs", tags=["processing"])

_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
    503: {"model": ErrorResponse},
}


class ProcessingJobNotFoundError(FaceIdError):
    """Raised when an administrative job lookup has no result."""

    status_code = status.HTTP_404_NOT_FOUND
    code = "processing_job_not_found"


class ProcessingJobConflictError(FaceIdError):
    """Raised when a requested transition is invalid for the job state."""

    status_code = status.HTTP_409_CONFLICT
    code = "processing_job_conflict"


class ProcessingJobResponse(BaseModel):
    """One job without infrastructure credentials or binary payloads."""

    job_uuid: UUID
    pipeline: str
    pipeline_version: str
    subject_type: str
    subject_uuid: UUID
    idempotency_key: str
    payload: dict[str, Any]
    status: JobStatus
    priority: str
    attempt_count: int
    max_attempts: int
    queued_at: datetime
    available_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    leased_until: datetime | None
    worker_id: str | None
    error_code: str | None
    error_detail: str | None


class ProcessingJobPage(BaseModel):
    """A bounded page of durable jobs."""

    items: list[ProcessingJobResponse]
    total: int
    limit: int
    offset: int


class ProcessingJobSummaryResponse(BaseModel):
    """Current job counts and queue age indicator."""

    counts: dict[str, int]
    oldest_queued_at: datetime | None


class ProcessingJobAttemptResponse(BaseModel):
    """One immutable execution attempt for a durable job."""

    attempt_uuid: UUID
    job_uuid: UUID
    attempt_number: int
    lease_uuid: UUID
    worker_id: str
    started_at: datetime
    completed_at: datetime | None
    outcome: str | None
    error_code: str | None
    error_detail: str | None


class ProcessingJobDetailResponse(ProcessingJobResponse):
    """A durable job together with its execution history."""

    attempts: list[ProcessingJobAttemptResponse] = Field(default_factory=list)


def _response(job: ProcessingJob) -> ProcessingJobResponse:
    return ProcessingJobResponse(
        job_uuid=job.job_uuid,
        pipeline=job.pipeline,
        pipeline_version=job.pipeline_version,
        subject_type=job.subject_type,
        subject_uuid=job.subject_uuid,
        idempotency_key=job.idempotency_key,
        payload=job.payload,
        status=job.status,
        priority=job.priority.name.lower(),
        attempt_count=job.attempt_count,
        max_attempts=job.max_attempts,
        queued_at=job.queued_at,
        available_at=job.available_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        leased_until=job.leased_until,
        worker_id=job.worker_id,
        error_code=job.error_code.value if job.error_code else None,
        error_detail=job.error_detail,
    )


@router.get(
    "",
    response_model=ProcessingJobPage,
    summary="List durable processing jobs",
    responses=_RESPONSES,
)
async def list_processing_jobs(
    administration: Annotated[
        ProcessingJobAdministration, Depends(get_processing_job_administration)
    ],
    _principal: Annotated[Principal, Depends(require(Scope.ADMIN))],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    pipeline: Annotated[str | None, Query(max_length=64)] = None,
    job_status: Annotated[JobStatus | None, Query(alias="status")] = None,
) -> ProcessingJobPage:
    """Return an administrator-filtered page of processing jobs."""
    page = await administration.list(
        limit=limit, offset=offset, pipeline=pipeline, status=job_status
    )
    return ProcessingJobPage(
        items=[_response(job) for job in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get(
    "/summary",
    response_model=ProcessingJobSummaryResponse,
    summary="Summarize durable processing state",
    responses=_RESPONSES,
)
async def processing_job_summary(
    administration: Annotated[
        ProcessingJobAdministration, Depends(get_processing_job_administration)
    ],
    _principal: Annotated[Principal, Depends(require(Scope.ADMIN))],
) -> ProcessingJobSummaryResponse:
    """Return operational queue counts for administrators."""
    counts = await administration.counts()
    return ProcessingJobSummaryResponse(
        counts={state.value: count for state, count in counts.by_status.items()},
        oldest_queued_at=counts.oldest_queued_at,
    )


@router.get(
    "/{job_uuid}",
    response_model=ProcessingJobDetailResponse,
    summary="Read a durable processing job",
    responses={**_RESPONSES, 404: {"model": ErrorResponse}},
)
async def read_processing_job(
    job_uuid: UUID,
    administration: Annotated[
        ProcessingJobAdministration, Depends(get_processing_job_administration)
    ],
    _principal: Annotated[Principal, Depends(require(Scope.ADMIN))],
) -> ProcessingJobDetailResponse:
    """Return one processing job and all recorded attempts."""
    job = await administration.get(job_uuid)
    if job is None:
        raise ProcessingJobNotFoundError(f"no processing job {job_uuid}")
    attempts = await administration.attempts(job_uuid)
    return ProcessingJobDetailResponse(
        **_response(job).model_dump(),
        attempts=[ProcessingJobAttemptResponse.model_validate(item) for item in attempts],
    )


async def _change_job(
    job_uuid: UUID,
    administration: ProcessingJobAdministration,
    principal: Principal,
    *,
    retry: bool,
) -> ProcessingJobResponse:
    """Apply and normalize an audited administrative state change."""
    try:
        if retry:
            job = await administration.retry(job_uuid, actor=principal.as_actor())
        else:
            job = await administration.cancel(job_uuid, actor=principal.as_actor())
    except KeyError as exc:
        raise ProcessingJobNotFoundError(str(exc)) from exc
    except JobStateError as exc:
        raise ProcessingJobConflictError(str(exc)) from exc
    return _response(job)


@router.post(
    "/{job_uuid}/retry",
    response_model=ProcessingJobResponse,
    summary="Retry terminal processing work",
    responses={
        **_RESPONSES,
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
async def retry_processing_job(
    job_uuid: UUID,
    administration: Annotated[
        ProcessingJobAdministration, Depends(get_processing_job_administration)
    ],
    principal: Annotated[Principal, Depends(require(Scope.ADMIN))],
) -> ProcessingJobResponse:
    """Return a terminal job to the runnable queue."""
    return await _change_job(job_uuid, administration, principal, retry=True)


@router.post(
    "/{job_uuid}/cancel",
    response_model=ProcessingJobResponse,
    summary="Cancel non-terminal processing work",
    responses={
        **_RESPONSES,
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
async def cancel_processing_job(
    job_uuid: UUID,
    administration: Annotated[
        ProcessingJobAdministration, Depends(get_processing_job_administration)
    ],
    principal: Annotated[Principal, Depends(require(Scope.ADMIN))],
) -> ProcessingJobResponse:
    """Cancel a queued, retrying, or running job."""
    return await _change_job(job_uuid, administration, principal, retry=False)
