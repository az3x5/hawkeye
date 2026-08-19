"""User accounts authenticated by password.

Passwords are a different problem from API tokens. A token is 256 bits of
randomness, so a fast hash is fine; a password is chosen by a person and must
be assumed guessable, so it is stretched with Argon2id and every failure path
is made indistinguishable from every other.

The plaintext is never stored, never logged, and never accepted as a
command-line argument — argv is visible to every process on the host and lands
in shell history.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable
from uuid import UUID, uuid4

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.domain.auth import Scope

#: Argon2id at the library's defaults: ~40ms per verify on this hardware, which
#: is slow enough to matter to an attacker and fast enough to serve a login.
_hasher = PasswordHasher()

#: Verified when no such account exists, so that a missing account costs the
#: same time as a wrong password. Without this, response timing tells an
#: attacker which email addresses are real.
_DUMMY_HASH = _hasher.hash("a password that is never anybody's")

MINIMUM_PASSWORD_LENGTH = 12


class PasswordPolicyError(ValueError):
    """The proposed password is not acceptable."""


class AuthenticationFailed(Exception):
    """The email or password was wrong, or the account cannot sign in.

    Deliberately one exception for every cause: telling a caller which part
    they got right is information they have not earned.
    """


@dataclass(frozen=True, slots=True)
class User:
    """An account that signs in with a password."""

    email: str
    password_hash: str
    scopes: frozenset[Scope]
    user_uuid: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    password_changed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    disabled_at: datetime | None = None
    last_login_at: datetime | None = None

    @property
    def active(self) -> bool:
        """Whether the account may sign in."""
        return self.disabled_at is None


def normalise_email(email: str) -> str:
    """Fold an email to its canonical form for storage and lookup.

    Case-insensitive, because nobody expects Admin@example.com and
    admin@example.com to be different accounts.
    """
    cleaned = email.strip().lower()
    if not cleaned or "@" not in cleaned or cleaned.startswith("@") or cleaned.endswith("@"):
        raise ValueError(f"not a usable email address: {email!r}")
    return cleaned


def check_password_policy(password: str, *, email: str | None = None) -> None:
    """Raise if a proposed password is unacceptable.

    Length is the only rule that reliably helps. Composition rules push people
    towards predictable substitutions, so the check here is length plus a
    refusal to reuse the email address.
    """
    if len(password) < MINIMUM_PASSWORD_LENGTH:
        raise PasswordPolicyError(f"password must be at least {MINIMUM_PASSWORD_LENGTH} characters")
    if password.strip() == "":
        raise PasswordPolicyError("password must not be only whitespace")
    if email is not None and password.lower() == normalise_email(email):
        raise PasswordPolicyError("password must not be the email address")


def hash_password(password: str) -> str:
    """Stretch a password for storage. The plaintext is not retained."""
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """Check a password against a stored hash.

    Passing None still performs a verification against a dummy hash, so that a
    missing account and a wrong password take the same time.
    """
    try:
        _hasher.verify(password_hash if password_hash is not None else _DUMMY_HASH, password)
    except (VerifyMismatchError, InvalidHashError):
        return False
    return password_hash is not None


def needs_rehash(password_hash: str) -> bool:
    """Whether a stored hash was made with weaker parameters than we now use."""
    return _hasher.check_needs_rehash(password_hash)


@runtime_checkable
class UserStore(Protocol):
    """Storage of password accounts."""

    async def add(self, user: User) -> User:
        """Create an account. Raises if the email is taken."""
        ...

    async def find_by_email(self, email: str) -> User | None:
        """Look an account up by its normalised email."""
        ...

    async def set_password(self, user_uuid: UUID, password_hash: str) -> bool:
        """Replace an account's password hash."""
        ...

    async def record_login(self, user_uuid: UUID, when: datetime) -> None:
        """Note that an account signed in successfully."""
        ...

    async def set_disabled(self, user_uuid: UUID, disabled: bool) -> bool:
        """Enable or disable an account."""
        ...

    async def list_all(self) -> Sequence[User]:
        """Return every account, newest first."""
        ...
