"""Authentication and authorisation.

Identity here is *established*, not asserted. Before this existed a reviewer
typed their own name into a form and the audit log recorded whatever they
typed, which made the log a record of claims rather than of people.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import UUID, uuid4

from app.domain.audit import Actor

#: Length of a minted token in bytes before encoding.
_TOKEN_BYTES = 32

#: Tokens are shown once and stored only as a hash, so a database disclosure
#: does not hand over working credentials.
_PREFIX = "faceid_"


class AuthenticationError(Exception):
    """No usable credential was presented."""


class AuthorisationError(Exception):
    """The caller is known but not permitted to do this."""


class Scope(StrEnum):
    """What a credential is allowed to do.

    Deliberately narrow and separate: the service that submits enrolments has
    no business confirming identity decisions, and a human reviewer has no
    business bulk-enrolling.
    """

    ENROL = "enrol"
    IDENTIFY = "identify"
    REVIEW = "review"
    ADMIN = "admin"


@dataclass(frozen=True, slots=True)
class Principal:
    """An authenticated caller."""

    token_uuid: UUID
    subject: str
    kind: str
    scopes: frozenset[Scope]

    def __post_init__(self) -> None:
        """Validate the principal's identity and kind."""
        if not self.subject.strip():
            raise ValueError("a principal must have a subject")
        if self.kind not in {"user", "service"}:
            raise ValueError(f"principal kind must be 'user' or 'service', got {self.kind!r}")

    def has(self, scope: Scope) -> bool:
        """Whether this principal holds ``scope``."""
        return scope in self.scopes

    def require(self, scope: Scope) -> None:
        """Raise unless this principal holds ``scope``."""
        if not self.has(scope):
            raise AuthorisationError(
                f"{self.subject} lacks the '{scope.value}' scope required for this action"
            )

    def as_actor(self) -> Actor:
        """Render the principal for the audit log.

        A service credential is recorded as a system actor: it is not a person,
        and the log must not suggest a human judged anything.
        """
        return Actor(identifier=self.subject, kind="user" if self.kind == "user" else "system")


@dataclass(frozen=True, slots=True)
class ApiToken:
    """A credential as stored. The secret itself is never held.

    ``expires_at`` of None means the credential never expires. That is
    permitted but should be rare: a credential with no end date is one that
    stays valid until somebody notices it has leaked.
    """

    token_uuid: UUID
    subject: str
    kind: str
    token_sha256: str
    scopes: frozenset[Scope]
    created_at: datetime
    disabled_at: datetime | None = None
    expires_at: datetime | None = None

    def expired(self, *, now: datetime | None = None) -> bool:
        """Whether the credential's lifetime has run out."""
        if self.expires_at is None:
            return False
        return (now or datetime.now(UTC)) >= self.expires_at

    def usable(self, *, now: datetime | None = None) -> bool:
        """Whether the credential may be used: neither revoked nor expired."""
        return self.disabled_at is None and not self.expired(now=now)

    @property
    def active(self) -> bool:
        """Whether the credential has been revoked. Prefer ``usable``."""
        return self.disabled_at is None


def generate_token() -> str:
    """Mint a new secret. Returned once; only its hash is ever stored."""
    return f"{_PREFIX}{secrets.token_urlsafe(_TOKEN_BYTES)}"


def hash_token(token: str) -> str:
    """Hash a presented secret for lookup.

    A plain SHA-256 rather than a password hash: these are 256-bit random
    secrets, not memorable passwords, so there is nothing for a slow hash to
    defend against and lookup must stay cheap enough to run per request.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token(
    subject: str,
    kind: str,
    scopes: Sequence[Scope],
    *,
    lifetime_days: int | None,
) -> tuple[ApiToken, str]:
    """Create a credential, returning the record and the one-time secret.

    ``lifetime_days`` of None mints a credential that never expires; callers
    must choose that deliberately rather than getting it by omission.
    """
    if lifetime_days is not None and lifetime_days < 1:
        raise ValueError(f"lifetime_days must be at least 1, got {lifetime_days}")

    issued = datetime.now(UTC)
    secret = generate_token()
    record = ApiToken(
        token_uuid=uuid4(),
        subject=subject,
        kind=kind,
        token_sha256=hash_token(secret),
        scopes=frozenset(scopes),
        created_at=issued,
        expires_at=None if lifetime_days is None else issued + timedelta(days=lifetime_days),
    )
    return record, secret


@runtime_checkable
class TokenStore(Protocol):
    """Storage of API credentials."""

    async def add(self, token: ApiToken) -> ApiToken:
        """Store a credential."""
        ...

    async def find_by_hash(self, token_sha256: str) -> ApiToken | None:
        """Look a credential up by the hash of the presented secret."""
        ...

    async def disable(self, token_uuid: UUID) -> bool:
        """Revoke a credential. Returns whether anything changed."""
        ...

    async def list_all(self) -> Sequence[ApiToken]:
        """Return every credential, newest first. Secrets are not recoverable."""
        ...
