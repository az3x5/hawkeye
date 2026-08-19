"""Account and credential administration.

Creating an account, changing a password, issuing a credential and revoking one
are all sensitive administrative actions, so each leaves an audit record naming
who did it. The records are written with the same append-only log that carries
review decisions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from app.domain.audit import Actor, AuditAction, AuditEvent, AuditLog
from app.domain.auth import ApiToken, Scope, TokenStore, new_token
from app.domain.users import (
    AuthenticationFailed,
    User,
    UserStore,
    check_password_policy,
    hash_password,
    normalise_email,
    verify_password,
)

logger = logging.getLogger(__name__)


class AccountNotFoundError(Exception):
    """No such account."""


class SelfLockoutError(Exception):
    """The action would lock the caller out of their own account."""


@dataclass(frozen=True, slots=True)
class IssuedToken:
    """A credential and its one-time secret."""

    token: ApiToken
    secret: str


class AccountAdministration:
    """Creates and manages password accounts, auditing every change."""

    def __init__(self, *, users: UserStore, tokens: TokenStore, audit: AuditLog) -> None:
        """Wire the service to account storage, credentials and the audit log."""
        self._users = users
        self._tokens = tokens
        self._audit = audit

    async def create(self, *, email: str, password: str, scopes: list[Scope], actor: Actor) -> User:
        """Create an account. The password is hashed before it is stored."""
        normalised = normalise_email(email)
        check_password_policy(password, email=normalised)

        user = User(
            email=normalised,
            password_hash=hash_password(password),
            scopes=frozenset(scopes),
        )
        await self._users.add(user)
        await self._record(
            AuditAction.ACCOUNT_CREATED,
            actor,
            {"email": user.email, "scopes": sorted(s.value for s in user.scopes)},
        )
        return user

    async def list_accounts(self) -> list[User]:
        """Return every account. Password hashes are not part of the response."""
        return list(await self._users.list_all())

    async def find(self, user_uuid: UUID) -> User:
        """Return one account, or raise."""
        for user in await self._users.list_all():
            if user.user_uuid == user_uuid:
                return user
        raise AccountNotFoundError(f"no account {user_uuid}")

    async def set_password(self, user_uuid: UUID, *, password: str, actor: Actor) -> User:
        """Replace an account's password and end the sessions it produced."""
        user = await self.find(user_uuid)
        check_password_policy(password, email=user.email)

        await self._users.set_password(user_uuid, hash_password(password))
        revoked = await self._tokens.disable_for_user(user_uuid)  # type: ignore[attr-defined]
        await self._record(
            AuditAction.ACCOUNT_PASSWORD_CHANGED,
            actor,
            {"email": user.email, "sessions_revoked": revoked},
        )
        return user

    async def change_own_password(
        self, *, user: User, current_password: str, new_password: str, actor: Actor
    ) -> None:
        """Change the caller's own password, proving they know the current one.

        Requiring the current password means a borrowed session cannot be
        turned into permanent ownership of the account.
        """
        if not verify_password(user.password_hash, current_password):
            raise AuthenticationFailed("current password is incorrect")
        if new_password == current_password:
            raise ValueError("the new password must differ from the current one")
        await self.set_password(user.user_uuid, password=new_password, actor=actor)

    async def set_disabled(
        self, user_uuid: UUID, *, disabled: bool, actor: Actor, caller_email: str
    ) -> User:
        """Disable or re-enable an account.

        Refuses to disable the caller's own account: an administrator locking
        themselves out is an accident, not an intention.
        """
        user = await self.find(user_uuid)
        if disabled and user.email == normalise_email(caller_email):
            raise SelfLockoutError("you cannot disable the account you are signed in as")

        await self._users.set_disabled(user_uuid, disabled)
        if disabled:
            await self._tokens.disable_for_user(user_uuid)  # type: ignore[attr-defined]
        await self._record(
            AuditAction.ACCOUNT_DISABLED if disabled else AuditAction.ACCOUNT_ENABLED,
            actor,
            {"email": user.email},
        )
        # Re-read rather than returning the copy fetched before the update, or
        # the caller is told the account is still active.
        return await self.find(user_uuid)

    async def _record(self, action: AuditAction, actor: Actor, details: dict[str, object]) -> None:
        await self._audit.record(AuditEvent(action=action, actor=actor, details=details))


class TokenAdministration:
    """Issues and revokes API credentials, auditing every change."""

    def __init__(self, *, tokens: TokenStore, audit: AuditLog) -> None:
        """Wire the service to credential storage and the audit log."""
        self._tokens = tokens
        self._audit = audit

    async def issue(
        self,
        *,
        subject: str,
        kind: str,
        scopes: list[Scope],
        lifetime_days: int | None,
        actor: Actor,
    ) -> IssuedToken:
        """Mint a credential. The secret is returned once and never stored."""
        record, secret = new_token(subject, kind, scopes, lifetime_days=lifetime_days)
        await self._tokens.add(record)
        await self._audit.record(
            AuditEvent(
                action=AuditAction.TOKEN_ISSUED,
                actor=actor,
                details={
                    "token_uuid": str(record.token_uuid),
                    "subject": record.subject,
                    "kind": record.kind,
                    "scopes": sorted(s.value for s in record.scopes),
                    "expires_at": record.expires_at.isoformat() if record.expires_at else None,
                },
            )
        )
        logger.info(
            "credential issued", extra={"subject": record.subject, "issued_by": actor.identifier}
        )
        return IssuedToken(token=record, secret=secret)

    async def list_tokens(self) -> list[ApiToken]:
        """Return every credential. Secrets are not recoverable, only metadata."""
        return list(await self._tokens.list_all())

    async def revoke(self, token_uuid: UUID, *, actor: Actor) -> bool:
        """Revoke a credential."""
        changed = await self._tokens.disable(token_uuid)
        if changed:
            await self._audit.record(
                AuditEvent(
                    action=AuditAction.TOKEN_REVOKED,
                    actor=actor,
                    details={"token_uuid": str(token_uuid)},
                )
            )
        return changed
