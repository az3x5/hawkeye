"""Redis connector: client lifecycle and the readiness probe."""

from __future__ import annotations

import logging

from redis.asyncio import Redis

logger = logging.getLogger(__name__)


class RedisConnector:
    """Owns the Redis client. Implements ``StorageConnector``."""

    provider_name = "redis"

    #: Read deadline for a single command. Must comfortably exceed the longest
    #: blocking call the queue makes: redis-py derives a blocking command's read
    #: deadline from the command's own timeout, so without headroom the client
    #: races the server and raises instead of returning "nothing arrived".
    DEFAULT_SOCKET_TIMEOUT_SECONDS = 30.0

    def __init__(self, dsn: str, *, socket_timeout: float | None = None) -> None:
        """Build a client for ``dsn``. No connection is opened until used."""
        self._socket_timeout = (
            self.DEFAULT_SOCKET_TIMEOUT_SECONDS if socket_timeout is None else socket_timeout
        )
        self._client: Redis = Redis.from_url(
            dsn, decode_responses=True, socket_timeout=self._socket_timeout
        )

    @property
    def socket_timeout(self) -> float:
        """The per-command read deadline in seconds."""
        return self._socket_timeout

    @property
    def provider(self) -> str:
        """Short provider identifier."""
        return self.provider_name

    @property
    def client(self) -> Redis:
        """The underlying client. Intended for the queue and tests."""
        return self._client

    async def ping(self) -> None:
        """Raise if Redis is not reachable and answering."""
        await self._client.ping()

    async def close(self) -> None:
        """Release the client's connections."""
        await self._client.aclose()
