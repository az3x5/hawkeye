"""Bucket-addressed blob storage on a filesystem.

The local development answer to S3. A bucket is a directory beneath the root
and a key is a path within it, so the on-disk layout mirrors what an object
listing would show and an operator can find a file by hand.

``ObjectLocation`` has already rejected traversal by the time a path is built
here, and ``_resolve`` checks the result stays inside the root anyway. Two
checks for the same property is deliberate: this class turns caller-supplied
strings into filesystem paths, which is exactly where a single missed
validation becomes an arbitrary write.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from app.domain.storage import BlobStoreError, ObjectLocation, StoredObject

logger = logging.getLogger(__name__)

#: Sidecar suffix holding the content type, which a filesystem cannot store.
_METADATA_SUFFIX = ".meta.json"

#: Suffix for a partially written object, so a reader never sees half a file.
_PARTIAL_SUFFIX = ".partial"


class FilesystemBlobStore:
    """``BlobStore`` backed by a directory tree."""

    provider_name = "filesystem-blob"

    def __init__(self, root: Path) -> None:
        """Store buckets beneath ``root``, creating it if needed."""
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def provider(self) -> str:
        """Short provider identifier."""
        return self.provider_name

    @property
    def root(self) -> Path:
        """The directory buckets are stored beneath."""
        return self._root

    async def ping(self) -> None:
        """Raise if the storage root is not writable."""
        await asyncio.to_thread(self._check_writable)

    def _check_writable(self) -> None:
        probe = self._root / ".writable"
        probe.write_bytes(b"")
        probe.unlink()

    def _resolve(self, location: ObjectLocation) -> Path:
        path = (self._root / location.bucket / location.key).resolve()
        if not path.is_relative_to(self._root):
            raise BlobStoreError(f"object address {location} resolves outside the storage root")
        return path

    async def ensure_bucket(self, bucket: str) -> None:
        """Create the bucket directory if it does not exist."""
        # Validate the name through the same type every other call goes via.
        ObjectLocation(bucket=bucket, key="_")
        await asyncio.to_thread(lambda: (self._root / bucket).mkdir(parents=True, exist_ok=True))

    async def put(self, location: ObjectLocation, data: bytes, *, content_type: str) -> None:
        """Store ``data`` at ``location``, writing then renaming into place."""
        path = self._resolve(location)
        await asyncio.to_thread(self._write, path, data, content_type)

    @staticmethod
    def _write(path: Path, data: bytes, content_type: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        metadata = path.with_name(path.name + _METADATA_SUFFIX)
        if path.exists() and metadata.exists():
            return
        temporary = path.with_name(path.name + _PARTIAL_SUFFIX)
        temporary.write_bytes(data)
        temporary.replace(path)
        metadata.write_text(
            json.dumps({"content_type": content_type, "size_bytes": len(data)}),
            encoding="utf-8",
        )

    async def get(self, location: ObjectLocation) -> bytes | None:
        """Return the stored bytes, or None if absent."""
        path = self._resolve(location)
        return await asyncio.to_thread(lambda: path.read_bytes() if path.is_file() else None)

    async def get_range(self, location: ObjectLocation, start: int, length: int) -> bytes | None:
        """Return ``length`` bytes from ``start`` without reading the whole object."""
        if start < 0 or length <= 0:
            raise BlobStoreError(f"invalid range: start={start} length={length}")
        path = self._resolve(location)

        def _read() -> bytes | None:
            if not path.is_file():
                return None
            with path.open("rb") as handle:
                handle.seek(start)
                return handle.read(length)

        return await asyncio.to_thread(_read)

    async def stat(self, location: ObjectLocation) -> StoredObject | None:
        """Return size, content type and modification time, or None if absent."""
        path = self._resolve(location)

        def _stat() -> StoredObject | None:
            if not path.is_file():
                return None
            stat = path.stat()
            content_type = "application/octet-stream"
            metadata = path.with_name(path.name + _METADATA_SUFFIX)
            if metadata.is_file():
                try:
                    content_type = json.loads(metadata.read_text(encoding="utf-8"))["content_type"]
                except (json.JSONDecodeError, KeyError, OSError):
                    logger.warning("unreadable object metadata at %s", metadata)
            return StoredObject(
                location=location,
                size_bytes=stat.st_size,
                content_type=content_type,
                modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
            )

        return await asyncio.to_thread(_stat)

    async def delete(self, location: ObjectLocation) -> bool:
        """Remove the object and its metadata sidecar."""
        path = self._resolve(location)

        def _remove() -> bool:
            existed = path.is_file()
            path.unlink(missing_ok=True)
            path.with_name(path.name + _METADATA_SUFFIX).unlink(missing_ok=True)
            return existed

        return await asyncio.to_thread(_remove)

    async def list_keys(
        self, bucket: str, *, prefix: str = "", older_than: datetime | None = None
    ) -> list[str]:
        """Return keys in ``bucket``, optionally filtered by prefix and age."""
        ObjectLocation(bucket=bucket, key="_")
        base = self._root / bucket

        def _scan() -> list[str]:
            if not base.is_dir():
                return []
            found: list[str] = []
            for path in base.rglob("*"):
                if not path.is_file():
                    continue
                name = path.name
                if name.endswith(_METADATA_SUFFIX) or name.endswith(_PARTIAL_SUFFIX):
                    continue
                key = path.relative_to(base).as_posix()
                if prefix and not key.startswith(prefix):
                    continue
                if older_than is not None:
                    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
                    if modified >= older_than:
                        continue
                found.append(key)
            return sorted(found)

        return await asyncio.to_thread(_scan)
