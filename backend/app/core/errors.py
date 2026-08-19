"""Structured error envelope shared by every externally visible endpoint."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException


class ErrorDetail(BaseModel):
    """Machine-readable description of a single failure."""

    code: str = Field(description="Stable, machine-readable error code.")
    message: str = Field(description="Human-readable explanation.")
    field: str | None = Field(
        default=None, description="Request field the error applies to, if any."
    )


class ErrorResponse(BaseModel):
    """Envelope returned for every non-2xx response."""

    error: ErrorDetail
    details: list[ErrorDetail] = Field(default_factory=list)


class FaceIdError(Exception):
    """Base class for errors that map onto the structured envelope."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"

    def __init__(self, message: str, *, field: str | None = None) -> None:
        """Record the human-readable ``message`` and optional offending ``field``."""
        super().__init__(message)
        self.message = message
        self.field = field

    def to_response(self) -> ErrorResponse:
        """Render this error into the shared envelope."""
        return ErrorResponse(
            error=ErrorDetail(code=self.code, message=self.message, field=self.field)
        )


class RateLimitedError(FaceIdError):
    """The caller has made too many requests."""

    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limited"

    def __init__(self, message: str, *, retry_after_seconds: int) -> None:
        """Record how long the caller should wait before retrying."""
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class ServiceUnavailableError(FaceIdError):
    """A required downstream dependency is not usable."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "service_unavailable"


def _json(status_code: int, payload: ErrorResponse) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=payload.model_dump())


def install_error_handlers(app: FastAPI) -> None:
    """Register handlers so all errors share one wire format."""

    @app.exception_handler(FaceIdError)
    async def _faceid(_: Request, exc: FaceIdError) -> JSONResponse:
        response = _json(exc.status_code, exc.to_response())
        if isinstance(exc, RateLimitedError):
            # Tell the caller when to come back rather than leaving them to
            # guess and retry into the same wall.
            response.headers["Retry-After"] = str(exc.retry_after_seconds)
        return response

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            ErrorDetail(
                code="invalid_value",
                message=err["msg"],
                field=".".join(str(p) for p in err["loc"][1:]) or None,
            )
            for err in exc.errors()
        ]
        payload = ErrorResponse(
            error=ErrorDetail(code="validation_error", message="Request validation failed."),
            details=details,
        )
        return _json(status.HTTP_422_UNPROCESSABLE_CONTENT, payload)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail: Any = exc.detail
        payload = ErrorResponse(
            error=ErrorDetail(
                code=f"http_{exc.status_code}",
                message=detail if isinstance(detail, str) else "Request failed.",
            )
        )
        return _json(exc.status_code, payload)
