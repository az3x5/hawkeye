"""Password sign-in.

Signing in with a password does not create a parallel authentication system: it
mints an ordinary short-lived credential. Everything downstream — scopes, the
audit log, revocation, rate limiting — keeps working unchanged, and there is
only one way to authenticate a request.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from app.domain.auth import ApiToken, Scope, new_token
from app.domain.users import (
    AuthenticationFailed,
    User,
    UserStore,
    hash_password,
    needs_rehash,
    normalise_email,
    verify_password,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Session:
    """A credential minted by signing in."""

    token: ApiToken
    secret: str
    user: User

    @property
    def scopes(self) -> frozenset[Scope]:
        """What the session may do."""
        return self.token.scopes


class AuthenticationService:
    """Turns an email and password into a session credential."""

    def __init__(
        self,
        *,
        users: UserStore,
        tokens: object,
        session_lifetime_seconds: int,
    ) -> None:
        """Wire the service to account and credential storage."""
        self._users = users
        self._tokens = tokens
        self._lifetime = session_lifetime_seconds

    async def sign_in(self, email: str, password: str) -> Session:
        """Verify a password and mint a session credential.

        Every failure raises the same exception with the same message. An
        unknown account still costs a password verification, so timing does not
        reveal which addresses are real.
        """
        try:
            normalised = normalise_email(email)
        except ValueError:
            verify_password(None, password)
            raise AuthenticationFailed("email or password is incorrect") from None

        user = await self._users.find_by_email(normalised)
        correct = verify_password(user.password_hash if user else None, password)

        if user is None or not correct or not user.active:
            logger.warning("failed sign-in attempt", extra={"email": normalised})
            raise AuthenticationFailed("email or password is incorrect")

        # Take the opportunity to upgrade a hash made with weaker parameters.
        if needs_rehash(user.password_hash):
            await self._users.set_password(user.user_uuid, hash_password(password))

        record, secret = new_token(
            subject=user.email,
            kind="user",
            scopes=sorted(user.scopes, key=lambda scope: scope.value),
            lifetime_days=None,
            lifetime_seconds=self._lifetime,
            user_uuid=user.user_uuid,
        )
        await self._tokens.add(record)  # type: ignore[attr-defined]
        await self._users.record_login(user.user_uuid, datetime.now(UTC))
        logger.info("signed in", extra={"subject": user.email})
        return Session(token=record, secret=secret, user=user)

    async def sign_out(self, token_uuid: object) -> bool:
        """Revoke one session credential."""
        return bool(await self._tokens.disable(token_uuid))  # type: ignore[attr-defined]

    async def change_password(self, user: User, new_password: str) -> None:
        """Set a new password and revoke every session the old one produced.

        The point of changing a password is that the old one stops granting
        access — including through sessions it already minted.
        """
        await self._users.set_password(user.user_uuid, hash_password(new_password))
        revoked = await self._tokens.disable_for_user(user.user_uuid)  # type: ignore[attr-defined]
        logger.info("password changed", extra={"subject": user.email, "sessions_revoked": revoked})
