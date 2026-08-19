"""Account and credential administration over HTTP.

Every endpoint here requires the ``admin`` scope and writes an audit record,
because each one changes who can do what. The single exception is changing
your own password, which any signed-in account may do by proving it knows the
current one.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, EmailStr, Field

from app.api.v1.dependencies import (
    get_account_administration,
    get_default_token_lifetime,
    get_token_administration,
)
from app.api.v1.security import get_principal, require
from app.core.errors import ErrorResponse, FaceIdError
from app.domain.auth import Principal, Scope
from app.domain.repositories import ConflictError
from app.domain.users import AuthenticationFailed, PasswordPolicyError
from app.services.administration import (
    AccountAdministration,
    AccountNotFoundError,
    SelfLockoutError,
    TokenAdministration,
)

router = APIRouter(tags=["administration"])

#: Applied to every endpoint in this module.
_ADMIN_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
    503: {"model": ErrorResponse},
}


class AccountNotFoundHttpError(FaceIdError):
    """No such account."""

    status_code = status.HTTP_404_NOT_FOUND
    code = "account_not_found"


class AccountConflictError(FaceIdError):
    """The account cannot be created or changed as asked."""

    status_code = status.HTTP_409_CONFLICT
    code = "account_conflict"


class InvalidPasswordError(FaceIdError):
    """The proposed password is not acceptable."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "invalid_password"


class WrongPasswordError(FaceIdError):
    """The current password was not correct."""

    status_code = status.HTTP_403_FORBIDDEN
    code = "wrong_password"


class TokenNotFoundError(FaceIdError):
    """No such credential, or it is already revoked."""

    status_code = status.HTTP_404_NOT_FOUND
    code = "token_not_found"


# --------------------------------------------------------------------------
# Accounts
# --------------------------------------------------------------------------


class AccountResponse(BaseModel):
    """An account. Never carries the password hash."""

    user_uuid: UUID
    email: str
    scopes: list[Scope]
    active: bool
    created_at: datetime
    last_login_at: datetime | None = None


class CreateAccountRequest(BaseModel):
    """A new account."""

    email: EmailStr
    password: str = Field(min_length=1, max_length=1024, repr=False)
    scopes: list[Scope] = Field(min_length=1, description="At least one scope is required.")


class SetPasswordRequest(BaseModel):
    """An administrator setting somebody's password."""

    password: str = Field(min_length=1, max_length=1024, repr=False)


class ChangeOwnPasswordRequest(BaseModel):
    """A signed-in account changing its own password."""

    current_password: str = Field(min_length=1, max_length=1024, repr=False)
    new_password: str = Field(min_length=1, max_length=1024, repr=False)


def _account(user: object) -> AccountResponse:
    return AccountResponse(
        user_uuid=user.user_uuid,  # type: ignore[attr-defined]
        email=user.email,  # type: ignore[attr-defined]
        scopes=sorted(user.scopes, key=lambda scope: scope.value),  # type: ignore[attr-defined]
        active=user.active,  # type: ignore[attr-defined]
        created_at=user.created_at,  # type: ignore[attr-defined]
        last_login_at=user.last_login_at,  # type: ignore[attr-defined]
    )


@router.post(
    "/accounts",
    response_model=AccountResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a password account",
    responses={**_ADMIN_RESPONSES, 409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def create_account(
    body: CreateAccountRequest,
    accounts: Annotated[AccountAdministration, Depends(get_account_administration)],
    principal: Annotated[Principal, Depends(require(Scope.ADMIN))],
) -> AccountResponse:
    """Create an account that signs in with a password."""
    try:
        user = await accounts.create(
            email=str(body.email),
            password=body.password,
            scopes=body.scopes,
            actor=principal.as_actor(),
        )
    except PasswordPolicyError as exc:
        raise InvalidPasswordError(str(exc)) from exc
    except ConflictError as exc:
        raise AccountConflictError(str(exc)) from exc
    return _account(user)


@router.get(
    "/accounts",
    response_model=list[AccountResponse],
    summary="List accounts",
    responses=_ADMIN_RESPONSES,
)
async def list_accounts(
    accounts: Annotated[AccountAdministration, Depends(get_account_administration)],
    _principal: Annotated[Principal, Depends(require(Scope.ADMIN))],
) -> list[AccountResponse]:
    """Return every account, newest first."""
    return [_account(user) for user in await accounts.list_accounts()]


@router.post(
    "/accounts/{user_uuid}/password",
    response_model=AccountResponse,
    summary="Set an account's password",
    responses={**_ADMIN_RESPONSES, 404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def set_account_password(
    user_uuid: UUID,
    body: SetPasswordRequest,
    accounts: Annotated[AccountAdministration, Depends(get_account_administration)],
    principal: Annotated[Principal, Depends(require(Scope.ADMIN))],
) -> AccountResponse:
    """Replace an account's password, ending every session it produced."""
    try:
        user = await accounts.set_password(
            user_uuid, password=body.password, actor=principal.as_actor()
        )
    except AccountNotFoundError as exc:
        raise AccountNotFoundHttpError(str(exc)) from exc
    except PasswordPolicyError as exc:
        raise InvalidPasswordError(str(exc)) from exc
    return _account(user)


@router.post(
    "/accounts/{user_uuid}/disable",
    response_model=AccountResponse,
    summary="Disable an account",
    responses={**_ADMIN_RESPONSES, 404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def disable_account(
    user_uuid: UUID,
    accounts: Annotated[AccountAdministration, Depends(get_account_administration)],
    principal: Annotated[Principal, Depends(require(Scope.ADMIN))],
) -> AccountResponse:
    """Disable an account and revoke its sessions."""
    return await _set_disabled(user_uuid, True, accounts, principal)


@router.post(
    "/accounts/{user_uuid}/enable",
    response_model=AccountResponse,
    summary="Re-enable an account",
    responses={**_ADMIN_RESPONSES, 404: {"model": ErrorResponse}},
)
async def enable_account(
    user_uuid: UUID,
    accounts: Annotated[AccountAdministration, Depends(get_account_administration)],
    principal: Annotated[Principal, Depends(require(Scope.ADMIN))],
) -> AccountResponse:
    """Re-enable a disabled account."""
    return await _set_disabled(user_uuid, False, accounts, principal)


async def _set_disabled(
    user_uuid: UUID,
    disabled: bool,
    accounts: AccountAdministration,
    principal: Principal,
) -> AccountResponse:
    try:
        user = await accounts.set_disabled(
            user_uuid,
            disabled=disabled,
            actor=principal.as_actor(),
            caller_email=principal.subject,
        )
    except AccountNotFoundError as exc:
        raise AccountNotFoundHttpError(str(exc)) from exc
    except SelfLockoutError as exc:
        raise AccountConflictError(str(exc)) from exc
    return _account(user)


@router.post(
    "/me/password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Change your own password",
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def change_own_password(
    body: ChangeOwnPasswordRequest,
    accounts: Annotated[AccountAdministration, Depends(get_account_administration)],
    principal: Annotated[Principal, Depends(get_principal)],
) -> None:
    """Change the signed-in account's password.

    Requires the current password: a borrowed session must not be convertible
    into permanent ownership of the account. Every session, including this one,
    is revoked afterwards.
    """
    users = await accounts.list_accounts()
    user = next((u for u in users if u.email == principal.subject.lower()), None)
    if user is None:
        raise AccountNotFoundHttpError("this credential does not belong to an account")

    try:
        await accounts.change_own_password(
            user=user,
            current_password=body.current_password,
            new_password=body.new_password,
            actor=principal.as_actor(),
        )
    except AuthenticationFailed as exc:
        raise WrongPasswordError(str(exc)) from exc
    except PasswordPolicyError as exc:
        raise InvalidPasswordError(str(exc)) from exc
    except ValueError as exc:
        raise InvalidPasswordError(str(exc)) from exc


# --------------------------------------------------------------------------
# Credentials
# --------------------------------------------------------------------------


class TokenResponse(BaseModel):
    """A credential's metadata. Never carries the secret."""

    token_uuid: UUID
    subject: str
    kind: str
    scopes: list[Scope]
    active: bool
    expired: bool
    created_at: datetime
    expires_at: datetime | None = None


class IssueTokenRequest(BaseModel):
    """A credential to mint."""

    subject: str = Field(min_length=1, max_length=256)
    kind: str = Field(default="service", pattern="^(user|service)$")
    scopes: list[Scope] = Field(min_length=1)
    expires_in_days: int | None = Field(
        default=None, ge=1, le=3650, description="Defaults to the configured lifetime."
    )
    never_expires: bool = Field(
        default=False,
        description=(
            "Mint a credential with no end date. Must be asked for explicitly: such a "
            "credential stays valid until somebody notices it has leaked."
        ),
    )


class IssuedTokenResponse(TokenResponse):
    """A newly minted credential, including its one-time secret."""

    token: str = Field(description="Shown once. Only its hash is stored.", repr=False)


def _token(record: object) -> TokenResponse:
    return TokenResponse(
        token_uuid=record.token_uuid,  # type: ignore[attr-defined]
        subject=record.subject,  # type: ignore[attr-defined]
        kind=record.kind,  # type: ignore[attr-defined]
        scopes=sorted(record.scopes, key=lambda scope: scope.value),  # type: ignore[attr-defined]
        active=record.active,  # type: ignore[attr-defined]
        expired=record.expired(),  # type: ignore[attr-defined]
        created_at=record.created_at,  # type: ignore[attr-defined]
        expires_at=record.expires_at,  # type: ignore[attr-defined]
    )


@router.post(
    "/tokens",
    response_model=IssuedTokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Issue an API credential",
    responses={**_ADMIN_RESPONSES, 422: {"model": ErrorResponse}},
)
async def issue_token(
    body: IssueTokenRequest,
    tokens: Annotated[TokenAdministration, Depends(get_token_administration)],
    principal: Annotated[Principal, Depends(require(Scope.ADMIN))],
    default_lifetime: Annotated[int, Depends(get_default_token_lifetime)],
) -> IssuedTokenResponse:
    """Mint a credential and return its secret once."""
    issued = await tokens.issue(
        subject=body.subject,
        kind=body.kind,
        scopes=body.scopes,
        lifetime_days=None if body.never_expires else (body.expires_in_days or default_lifetime),
        actor=principal.as_actor(),
    )
    base = _token(issued.token)
    return IssuedTokenResponse(**base.model_dump(), token=issued.secret)


@router.get(
    "/tokens",
    response_model=list[TokenResponse],
    summary="List issued credentials",
    responses=_ADMIN_RESPONSES,
)
async def list_tokens(
    tokens: Annotated[TokenAdministration, Depends(get_token_administration)],
    _principal: Annotated[Principal, Depends(require(Scope.ADMIN))],
) -> list[TokenResponse]:
    """Return every credential's metadata. Secrets are not recoverable."""
    return [_token(record) for record in await tokens.list_tokens()]


@router.delete(
    "/tokens/{token_uuid}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a credential",
    responses={**_ADMIN_RESPONSES, 404: {"model": ErrorResponse}},
)
async def revoke_token(
    token_uuid: UUID,
    tokens: Annotated[TokenAdministration, Depends(get_token_administration)],
    principal: Annotated[Principal, Depends(require(Scope.ADMIN))],
) -> None:
    """Revoke a credential. Takes effect on its next request."""
    if not await tokens.revoke(token_uuid, actor=principal.as_actor()):
        raise TokenNotFoundError(f"{token_uuid} is unknown or already revoked")
