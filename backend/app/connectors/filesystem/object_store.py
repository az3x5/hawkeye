"""Content-addressed image storage on a filesystem.

Deliberately behind the ``ObjectStore`` interface: a deployment that wants S3
or GCS replaces this class and nothing else changes. Objects are addressed by
the SHA-256 of their contents, so the same image stored twice occupies one
place and the address is verifiable.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")

#: Objects are sharded by the first byte of the digest to keep directories small.
_SHARD_CHARS = 2


class ObjectStoreError(Exception):
    """The object store could not satisfy the request."""


def sha256_bytes(data: bytes) -> str:
    """Return the SHA-256 of ``data`` as lowercase hex."""
    return hashlib.sha256(data).hexdigest()


class FilesystemObjectStore:
    """``ObjectStore`` backed by a directory tree."""

    provider_name = "filesystem"

    def __init__(self, root: Path) -> None:
        """Store objects beneath ``root``, creating it if needed."""
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def provider(self) -> str:
        """Short provider identifier."""
        return self.provider_name

    @property
    def root(self) -> Path:
        """The directory objects are stored beneath."""
        return self._root

    async def ping(self) -> None:
        """Raise if the object root is not writable."""
        await asyncio.to_thread(self._check_writable)

    def _check_writable(self) -> None:
        probe = self._root / ".writable"
        probe.write_bytes(b"")
        probe.unlink()

    def _path_for(self, digest: str) -> Path:
        if not _DIGEST_RE.match(digest):
            raise ObjectStoreError(
                f"object addresses must be 64 lowercase hex characters, got {digest!r}"
            )
        return self._root / digest[:_SHARD_CHARS] / digest

    async def put(self, digest: str, data: bytes) -> None:
        """Store bytes under their content hash.

        Verifies the address matches the content, so a mismatch is caught here
        rather than becoming an unreadable object later.
        """
        actual = sha256_bytes(data)
        if actual != digest:
            raise ObjectStoreError(
                f"refusing to store object under {digest}: its contents hash to {actual}"
            )
        await asyncio.to_thread(self._write, self._path_for(digest), data)

    @staticmethod
    def _write(path: Path, data: bytes) -> None:
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write then rename, so a reader never sees a half-written object.
        temporary = path.with_suffix(".partial")
        temporary.write_bytes(data)
        temporary.replace(path)

    async def get(self, digest: str) -> bytes | None:
        """Return the stored bytes, or None if absent."""
        path = self._path_for(digest)
        return await asyncio.to_thread(lambda: path.read_bytes() if path.is_file() else None)

    async def delete(self, digest: str) -> bool:
        """Remove the object. Returns whether anything was removed."""
        path = self._path_for(digest)

        def _remove() -> bool:
            if not path.is_file():
                return False
            path.unlink()
            return True

        return await asyncio.to_thread(_remove)
