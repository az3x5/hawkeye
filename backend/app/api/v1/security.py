"""Authentication and scope enforcement for the HTTP layer."""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.v1.dependencies import _postgres
from app.connectors.postgres.tokens import SqlAlchemyTokenStore
from app.connectors.redis import RateLimit
from app.core.errors import FaceIdError, RateLimitedError
from app.domain.auth import AuthorisationError, Principal, Scope, hash_token

logger = logging.getLogger(__name__)

#: auto_error is off so a missing credential produces our own envelope rather
#: than FastAPI's, keeping every failure in one wire format.
_bearer = HTTPBearer(auto_error=False)


class NotAuthenticatedError(FaceIdError):
    """No usable credential was presented."""

    status_code = status.HTTP_401_UNAUTHORIZED
    code = "not_authenticated"


class NotAuthorisedError(FaceIdError):
    """The caller is known but not permitted to do this."""

    status_code = status.HTTP_403_FORBIDDEN
    code = "not_authorised"


async def get_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    """Resolve the bearer token to an authenticated principal."""
    if credentials is None or not credentials.credentials.strip():
        raise NotAuthenticatedError("a bearer token is required")

    async with _postgres(request).session() as session:
        token = await SqlAlchemyTokenStore(session).find_by_hash(
            hash_token(credentials.credentials)
        )

    # Unknown, revoked and expired credentials are reported identically:
    # telling a caller which one they hold is information they have not earned.
    if token is None or not token.usable():
        logger.warning("rejected an unusable credential")
        raise NotAuthenticatedError("the presented credential is not valid")

    return Principal(
        token_uuid=token.token_uuid,
        subject=token.subject,
        kind=token.kind,
        scopes=token.scopes,
        expires_at=token.expires_at,
    )


def rate_limited(
    scope: Scope, action: str, limit_for: Callable[[Any], int]
) -> Callable[..., Coroutine[Any, Any, Principal]]:
    """Build a dependency enforcing ``scope`` and a per-credential rate limit.

    The limit is keyed on the credential, not the network address: a stolen
    token is the thing worth throttling, and callers behind one gateway should
    not throttle each other.
    """

    async def dependency(
        request: Request,
        principal: Annotated[Principal, Depends(get_principal)],
    ) -> Principal:
        try:
            principal.require(scope)
        except AuthorisationError as exc:
            raise NotAuthorisedError(str(exc)) from exc

        limiter = getattr(request.app.state, "rate_limiter", None)
        if limiter is None:
            # No limiter configured: fail open rather than refuse real work,
            # but say so, because an unlimited endpoint is worth noticing.
            logger.warning("rate limiting is not configured", extra={"action": action})
            return principal

        settings = request.app.state.settings
        verdict = await limiter.check(
            str(principal.token_uuid),
            action,
            RateLimit(
                limit=limit_for(settings),
                window_seconds=settings.rate_limit_window_seconds,
            ),
        )
        if not verdict.allowed:
            raise RateLimitedError(
                f"too many '{action}' requests; retry in {verdict.retry_after_seconds}s",
                retry_after_seconds=verdict.retry_after_seconds,
            )
        return principal

    return dependency


def require(scope: Scope) -> Callable[[Principal], Coroutine[Any, Any, Principal]]:
    """Build a dependency admitting only principals holding ``scope``."""

    async def dependency(
        principal: Annotated[Principal, Depends(get_principal)],
    ) -> Principal:
        try:
            principal.require(scope)
        except AuthorisationError as exc:
            logger.warning(
                "rejected an unauthorised request",
                extra={"subject": principal.subject, "required_scope": scope.value},
            )
            raise NotAuthorisedError(str(exc)) from exc
        return principal

    return dependency
