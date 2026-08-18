"""PostgreSQL connector: engine lifecycle, sessions and the readiness probe."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


class PostgresConnector:
    """Owns the connection pool for the metadata store.

    Implements ``StorageConnector``. Nothing outside this package should know
    which driver or ORM is in use.
    """

    provider_name = "postgres"

    def __init__(self, dsn: str, *, pool_size: int = 5, echo: bool = False) -> None:
        """Build an engine for ``dsn``. No connection is opened until used."""
        self._engine: AsyncEngine = create_async_engine(
            _as_asyncpg_dsn(dsn),
            pool_size=pool_size,
            pool_pre_ping=True,
            echo=echo,
        )
        self._session_factory = async_sessionmaker(
            self._engine, expire_on_commit=False, class_=AsyncSession
        )

    @property
    def provider(self) -> str:
        """Short provider identifier."""
        return self.provider_name

    @property
    def engine(self) -> AsyncEngine:
        """The underlying engine. Intended for migrations and tests."""
        return self._engine

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Yield a session in a transaction, committing on clean exit.

        Any exception propagates after the transaction is rolled back — a
        failed write is never swallowed.
        """
        async with self._session_factory() as session:
            async with session.begin():
                yield session

    async def ping(self) -> None:
        """Raise if the database is not reachable and answering queries."""
        async with self._engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def close(self) -> None:
        """Dispose of the connection pool."""
        await self._engine.dispose()


def _as_asyncpg_dsn(dsn: str) -> str:
    """Force the asyncpg driver onto a plain ``postgresql://`` URL.

    Settings carry a driver-agnostic DSN so that the choice of driver stays an
    implementation detail of this connector.
    """
    if dsn.startswith("postgresql+"):
        return dsn
    if dsn.startswith("postgresql://"):
        return dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    raise ValueError(f"unsupported PostgreSQL DSN scheme: {dsn!r}")
