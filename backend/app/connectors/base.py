"""Storage connector seam.

Every storage provider (object storage, metadata store, vector store) is
reached through a connector so the application never depends on a specific
provider SDK. Concrete connectors are introduced in later phases.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class StorageConnector(Protocol):
    """Common contract for every storage provider binding."""

    @property
    def provider(self) -> str:
        """Short provider identifier, e.g. ``postgres`` or ``qdrant``."""
        ...

    async def ping(self) -> None:
        """Raise if the provider is not reachable and usable."""
        ...
