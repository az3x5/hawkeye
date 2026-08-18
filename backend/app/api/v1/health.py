"""Liveness and readiness endpoints."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings
from app.core.errors import ErrorResponse
from app.core.readiness import DependencyStatus, check_readiness

router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    """Process-level liveness."""

    status: Literal["ok"] = "ok"
    service: str = Field(description="Service identifier.")
    environment: str = Field(description="Deployment environment name.")


class ReadinessResponse(BaseModel):
    """Aggregate readiness across registered dependencies."""

    status: Literal["ready", "not_ready"]
    checks: list[DependencyStatus] = Field(
        default_factory=list,
        description=(
            "One entry per registered dependency probe. Empty while no "
            "dependency has been wired up yet; readiness then reflects the "
            "process only."
        ),
    )


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness probe",
    responses={500: {"model": ErrorResponse}},
)
async def health() -> HealthResponse:
    """Report that the process is running. Performs no I/O."""
    settings: Settings = get_settings()
    return HealthResponse(service=settings.service_name, environment=settings.environment)


@router.get(
    "/readyz",
    response_model=ReadinessResponse,
    summary="Readiness probe",
    responses={503: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
async def readyz(response: Response) -> ReadinessResponse:
    """Probe every registered dependency and report the aggregate result.

    Returns 503 when any dependency probe fails, so orchestrators can act on
    the status code without parsing the body.
    """
    checks = await check_readiness()
    ready = all(check.healthy for check in checks)
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(status="ready" if ready else "not_ready", checks=checks)
