"""API credential storage over PostgreSQL."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.postgres.tables import api_tokens
from app.domain.auth import ApiToken, Scope


class SqlAlchemyTokenStore:
    """``TokenStore`` over the ``api_tokens`` table."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the store to an open session."""
        self._session = session

    async def add(self, token: ApiToken) -> ApiToken:
        """Store a credential."""
        await self._session.execute(
            api_tokens.insert().values(
                token_uuid=token.token_uuid,
                subject=token.subject,
                kind=token.kind,
                token_sha256=token.token_sha256,
                scopes=sorted(scope.value for scope in token.scopes),
                created_at=token.created_at,
                disabled_at=token.disabled_at,
            )
        )
        return token

    async def find_by_hash(self, token_sha256: str) -> ApiToken | None:
        """Look a credential up by the hash of the presented secret."""
        result = await self._session.execute(
            select(api_tokens).where(api_tokens.c.token_sha256 == token_sha256)
        )
        row = result.one_or_none()
        return self._to_token(row) if row is not None else None

    async def disable(self, token_uuid: UUID) -> bool:
        """Revoke a credential."""
        result = await self._session.execute(
            api_tokens.update()
            .where(
                api_tokens.c.token_uuid == token_uuid,
                api_tokens.c.disabled_at.is_(None),
            )
            .values(disabled_at=datetime.now(UTC))
        )
        return cast("CursorResult[Any]", result).rowcount > 0

    async def list_all(self) -> Sequence[ApiToken]:
        """Return every credential, newest first."""
        result = await self._session.execute(
            select(api_tokens).order_by(api_tokens.c.created_at.desc())
        )
        return [self._to_token(row) for row in result.all()]

    @staticmethod
    def _to_token(row: Any) -> ApiToken:
        return ApiToken(
            token_uuid=row.token_uuid,
            subject=row.subject,
            kind=row.kind,
            token_sha256=row.token_sha256,
            # Unknown scope names are dropped rather than crashing the lookup:
            # a scope removed from the code must not lock everyone out.
            scopes=frozenset(Scope(s) for s in row.scopes if s in set(Scope)),
            created_at=row.created_at,
            disabled_at=row.disabled_at,
        )
