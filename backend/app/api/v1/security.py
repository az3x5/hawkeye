"""Authentication and scope enforcement for the HTTP layer."""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.v1.dependencies import _postgres
from app.connectors.postgres.tokens import SqlAlchemyTokenStore
from app.core.errors import FaceIdError
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

    # A revoked credential and an unknown one are reported identically: telling
    # a caller which one they hold is information they have not earned.
    if token is None or not token.active:
        logger.warning("rejected an unusable credential")
        raise NotAuthenticatedError("the presented credential is not valid")

    return Principal(
        token_uuid=token.token_uuid,
        subject=token.subject,
        kind=token.kind,
        scopes=token.scopes,
    )


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
