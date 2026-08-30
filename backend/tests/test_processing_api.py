"""HTTP contracts for durable processing administration."""

from __future__ import annotations

import builtins
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.dependencies import get_processing_job_administration
from app.api.v1.processing_jobs import router
from app.core.errors import ErrorResponse, install_error_handlers
from app.domain.auth import Scope
from app.domain.processing import JobCounts, JobStateError, JobStatus, ProcessingJob
from app.services.processing import ProcessingJobPage

from .conftest import authenticate


def _job(*, status: JobStatus = JobStatus.QUEUED) -> ProcessingJob:
    subject_uuid = uuid4()
    return ProcessingJob(
        pipeline="face_embedding",
        pipeline_version="1",
        subject_type="face_sample",
        subject_uuid=subject_uuid,
        idempotency_key=str(subject_uuid),
        payload={"face_sample_uuid": str(subject_uuid)},
        status=status,
    )


class FakeAdministration:
    """Small stateful substitute that keeps HTTP tests infrastructure-free."""

    def __init__(self) -> None:
        self.jobs: dict[UUID, ProcessingJob] = {}

    async def get(self, job_uuid: UUID) -> ProcessingJob | None:
        return self.jobs.get(job_uuid)

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        pipeline: str | None,
        status: JobStatus | None,
    ) -> ProcessingJobPage:
        del pipeline, status
        items = builtins.list(self.jobs.values())
        return ProcessingJobPage(
            items=items,
            total=len(items),
            limit=limit,
            offset=offset,
        )

    async def attempts(self, job_uuid: UUID) -> builtins.list[dict[str, object]]:
        del job_uuid
        return []

    async def counts(self) -> JobCounts:
        counts: dict[JobStatus, int] = {}
        for job in self.jobs.values():
            counts[job.status] = counts.get(job.status, 0) + 1
        queued = [job.queued_at for job in self.jobs.values() if job.status is JobStatus.QUEUED]
        return JobCounts(by_status=counts, oldest_queued_at=min(queued) if queued else None)

    async def retry(self, job_uuid: UUID, **kwargs: object) -> ProcessingJob:
        del kwargs
        job = self.jobs.get(job_uuid)
        if job is None:
            raise KeyError(job_uuid)
        if job.status not in {JobStatus.FAILED, JobStatus.DEAD_LETTER, JobStatus.CANCELLED}:
            raise JobStateError("not retryable")
        retried = _job(status=JobStatus.RETRY)
        object.__setattr__(retried, "job_uuid", job_uuid)
        self.jobs[job_uuid] = retried
        return retried

    async def cancel(self, job_uuid: UUID, **kwargs: object) -> ProcessingJob:
        del kwargs
        job = self.jobs.get(job_uuid)
        if job is None:
            raise KeyError(job_uuid)
        if job.status is JobStatus.COMPLETED:
            raise JobStateError("already terminal")
        cancelled = _job(status=JobStatus.CANCELLED)
        object.__setattr__(cancelled, "job_uuid", job_uuid)
        self.jobs[job_uuid] = cancelled
        return cancelled


@pytest.fixture
def administration() -> FakeAdministration:
    return FakeAdministration()


@pytest.fixture
def app(administration: FakeAdministration) -> FastAPI:
    application = FastAPI()
    install_error_handlers(application)
    application.include_router(router, prefix="/api/v1")
    application.dependency_overrides[get_processing_job_administration] = lambda: administration
    authenticate(application, Scope.ADMIN)
    return application


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def test_list_and_summary_expose_live_durable_state(
    client: TestClient, administration: FakeAdministration
) -> None:
    stored = _job()
    administration.jobs[stored.job_uuid] = stored
    listing = client.get("/api/v1/processing/jobs")
    summary = client.get("/api/v1/processing/jobs/summary")
    assert listing.status_code == 200
    assert listing.json()["items"][0]["job_uuid"] == str(stored.job_uuid)
    assert listing.json()["items"][0]["payload"] == stored.payload
    assert summary.json()["counts"] == {"queued": 1}
    oldest = datetime.fromisoformat(summary.json()["oldest_queued_at"])
    assert oldest == stored.queued_at


def test_detail_returns_attempt_history(
    client: TestClient, administration: FakeAdministration
) -> None:
    stored = _job()
    administration.jobs[stored.job_uuid] = stored
    response = client.get(f"/api/v1/processing/jobs/{stored.job_uuid}")
    assert response.status_code == 200
    assert response.json()["attempts"] == []


def test_unknown_job_is_structured_404(client: TestClient) -> None:
    response = client.get(f"/api/v1/processing/jobs/{uuid4()}")
    assert response.status_code == 404
    assert ErrorResponse.model_validate(response.json()).error.code == "processing_job_not_found"


def test_invalid_transition_is_structured_409(
    client: TestClient, administration: FakeAdministration
) -> None:
    stored = _job(status=JobStatus.COMPLETED)
    administration.jobs[stored.job_uuid] = stored
    response = client.post(f"/api/v1/processing/jobs/{stored.job_uuid}/cancel")
    assert response.status_code == 409
    assert ErrorResponse.model_validate(response.json()).error.code == "processing_job_conflict"


def test_cancel_then_retry_changes_operator_state(
    client: TestClient, administration: FakeAdministration
) -> None:
    stored = _job()
    administration.jobs[stored.job_uuid] = stored
    cancelled = client.post(f"/api/v1/processing/jobs/{stored.job_uuid}/cancel")
    retried = client.post(f"/api/v1/processing/jobs/{stored.job_uuid}/retry")
    assert cancelled.json()["status"] == "cancelled"
    assert retried.json()["status"] == "retry"


def test_processing_admin_requires_admin_scope(
    app: FastAPI, administration: FakeAdministration
) -> None:
    del administration
    authenticate(app, Scope.REVIEW)
    with TestClient(app) as restricted:
        response = restricted.get("/api/v1/processing/jobs")
    assert response.status_code == 403


def test_summary_timestamp_is_timezone_aware(
    client: TestClient, administration: FakeAdministration
) -> None:
    stored = _job()
    administration.jobs[stored.job_uuid] = stored
    value = client.get("/api/v1/processing/jobs/summary").json()["oldest_queued_at"]
    assert datetime.fromisoformat(value).utcoffset() == UTC.utcoffset(None)
