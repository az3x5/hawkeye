"""Password account storage over PostgreSQL."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.postgres.tables import users
from app.domain.auth import Scope
from app.domain.repositories import ConflictError
from app.domain.users import User, normalise_email


class SqlAlchemyUserStore:
    """``UserStore`` over the ``users`` table."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the store to an open session."""
        self._session = session

    async def add(self, user: User) -> User:
        """Create an account."""
        try:
            await self._session.execute(
                users.insert().values(
                    user_uuid=user.user_uuid,
                    email=user.email,
                    password_hash=user.password_hash,
                    scopes=sorted(scope.value for scope in user.scopes),
                    created_at=user.created_at,
                    password_changed_at=user.password_changed_at,
                    disabled_at=user.disabled_at,
                    last_login_at=user.last_login_at,
                )
            )
        except IntegrityError as exc:
            raise ConflictError(f"an account already exists for {user.email}") from exc
        return user

    async def find_by_email(self, email: str) -> User | None:
        """Look an account up by its normalised email."""
        try:
            normalised = normalise_email(email)
        except ValueError:
            return None
        result = await self._session.execute(select(users).where(users.c.email == normalised))
        row = result.one_or_none()
        return self._to_user(row) if row is not None else None

    async def set_password(self, user_uuid: UUID, password_hash: str) -> bool:
        """Replace an account's password hash."""
        result = await self._session.execute(
            users.update()
            .where(users.c.user_uuid == user_uuid)
            .values(password_hash=password_hash, password_changed_at=datetime.now(UTC))
        )
        return cast("CursorResult[Any]", result).rowcount > 0

    async def record_login(self, user_uuid: UUID, when: datetime) -> None:
        """Note that an account signed in successfully."""
        await self._session.execute(
            users.update().where(users.c.user_uuid == user_uuid).values(last_login_at=when)
        )

    async def set_disabled(self, user_uuid: UUID, disabled: bool) -> bool:
        """Enable or disable an account."""
        result = await self._session.execute(
            users.update()
            .where(users.c.user_uuid == user_uuid)
            .values(disabled_at=datetime.now(UTC) if disabled else None)
        )
        return cast("CursorResult[Any]", result).rowcount > 0

    async def list_all(self) -> Sequence[User]:
        """Return every account, newest first."""
        result = await self._session.execute(select(users).order_by(users.c.created_at.desc()))
        return [self._to_user(row) for row in result.all()]

    @staticmethod
    def _to_user(row: Any) -> User:
        return User(
            user_uuid=row.user_uuid,
            email=row.email,
            password_hash=row.password_hash,
            # Unknown scope names are dropped rather than crashing a login: a
            # scope removed from the code must not lock everyone out.
            scopes=frozenset(Scope(s) for s in row.scopes if s in set(Scope)),
            created_at=row.created_at,
            password_changed_at=row.password_changed_at,
            disabled_at=row.disabled_at,
            last_login_at=row.last_login_at,
        )
