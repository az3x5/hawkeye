"""Qdrant connector: client lifecycle and the readiness probe."""

from __future__ import annotations

import logging

from qdrant_client import AsyncQdrantClient

logger = logging.getLogger(__name__)


class QdrantConnector:
    """Owns the client for the vector store.

    Implements ``StorageConnector``. Qdrant holds biometric material and is
    never exposed publicly; this connector assumes an internal-network address.
    """

    provider_name = "qdrant"

    def __init__(self, url: str, *, api_key: str | None = None, timeout: int = 10) -> None:
        """Build a client for ``url``. No connection is opened until used."""
        self._client = AsyncQdrantClient(
            url=url,
            api_key=api_key,
            timeout=timeout,
            # The client's version handshake runs on a background thread and
            # raises there when the server is unreachable, which turns a clean
            # connection error into an unhandled thread exception. Readiness is
            # reported by ping() instead.
            check_compatibility=False,
        )

    @property
    def provider(self) -> str:
        """Short provider identifier."""
        return self.provider_name

    @property
    def client(self) -> AsyncQdrantClient:
        """The underlying client. Intended for repositories and tests."""
        return self._client

    async def ping(self) -> None:
        """Raise if the vector store is not reachable and answering."""
        await self._client.get_collections()

    async def close(self) -> None:
        """Release the client's connections."""
        await self._client.close()
