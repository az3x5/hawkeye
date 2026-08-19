"""Password sign-in and sign-out."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, EmailStr, Field

from app.api.v1.dependencies import get_authentication_service
from app.api.v1.security import get_principal
from app.connectors.redis import RateLimit
from app.core.errors import ErrorResponse, FaceIdError, RateLimitedError
from app.domain.auth import Principal, Scope
from app.domain.users import AuthenticationFailed
from app.services.authentication import AuthenticationService

router = APIRouter(tags=["authentication"])


class SignInFailedError(FaceIdError):
    """The email or password was wrong."""

    status_code = status.HTTP_401_UNAUTHORIZED
    code = "sign_in_failed"


class SignInRequest(BaseModel):
    """Credentials presented at sign-in."""

    email: EmailStr = Field(description="The account's email address.")
    password: str = Field(min_length=1, max_length=1024, repr=False)


class SessionResponse(BaseModel):
    """A newly minted session credential.

    The token is returned once. It is stored only as a hash, exactly like an
    issued API token, and behaves like one for every subsequent request.
    """

    token: str = Field(description="Bearer token for subsequent requests.", repr=False)
    subject: str
    scopes: list[Scope]
    expires_at: datetime | None


@router.post(
    "/sessions",
    response_model=SessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Sign in with an email and password",
    responses={
        401: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def sign_in(
    request: Request,
    body: SignInRequest,
    service: Annotated[AuthenticationService, Depends(get_authentication_service)],
) -> SessionResponse:
    """Exchange an email and password for a short-lived scoped credential.

    Rate limited per email address: a password is guessable in a way a token is
    not, so the number of attempts has to be bounded.
    """
    await _guard_attempts(request, body.email.lower())

    try:
        session = await service.sign_in(body.email, body.password)
    except AuthenticationFailed as exc:
        raise SignInFailedError(str(exc)) from exc

    return SessionResponse(
        token=session.secret,
        subject=session.token.subject,
        scopes=sorted(session.scopes, key=lambda scope: scope.value),
        expires_at=session.token.expires_at,
    )


@router.delete(
    "/sessions/current",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Sign out, revoking the presented session",
    responses={401: {"model": ErrorResponse}},
)
async def sign_out(
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[AuthenticationService, Depends(get_authentication_service)],
) -> None:
    """Revoke the credential used to make this request."""
    await service.sign_out(principal.token_uuid)


async def _guard_attempts(request: Request, email: str) -> None:
    limiter = getattr(request.app.state, "rate_limiter", None)
    if limiter is None:
        return
    settings = request.app.state.settings
    verdict = await limiter.check(
        email,
        "sign-in",
        RateLimit(
            limit=settings.rate_limit_sign_in,
            window_seconds=settings.rate_limit_window_seconds,
        ),
    )
    if not verdict.allowed:
        raise RateLimitedError(
            f"too many sign-in attempts; retry in {verdict.retry_after_seconds}s",
            retry_after_seconds=verdict.retry_after_seconds,
        )
