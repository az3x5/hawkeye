"""Blob storage seam.

The face pipeline addresses images by content hash alone, which was enough
while one filesystem directory held every object. A multimodal platform needs
more: media of different classifications must be separable, and the same code
must run against a local directory during development and against S3 in
production.

So the address here is a ``bucket`` and a ``key``, the two things every
object store in use has. ``BlobStore`` is the only storage vocabulary the
domain and service layers know; the filesystem and S3 implementations live in
``app.connectors`` and are interchangeable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

#: S3 bucket naming, restricted to the subset that is also a safe directory
#: name. Enforced here rather than at each call site so a filesystem store can
#: never be talked into escaping its root.
_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")

#: Keys may contain slashes to form a hierarchy, but never a segment that
#: walks upwards and never a leading slash that would reset the path.
_KEY_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")

#: Longest key accepted. S3 allows 1024 bytes; staying under it keeps every
#: backend able to store what any other backend accepted.
MAX_KEY_LENGTH = 512


class BlobStoreError(Exception):
    """The blob store could not satisfy the request."""


class ObjectNotFoundError(BlobStoreError):
    """No object exists at the requested location."""


class StorageDomain(StrEnum):
    """Which storage domain an object belongs to.

    This is not decoration. Reference biometrics and general media have
    different authorisation, retention and, in a deployed system, different
    credentials. Naming the domain in the type system means a caller has to
    choose one deliberately instead of defaulting into the wrong bucket.
    """

    #: Immutable bytes exactly as received, of any modality.
    MEDIA = "media"
    #: Artifacts computed from media: thumbnails, crops, extracted audio.
    DERIVED = "derived"
    #: Deliberately enrolled biometric material from a trusted identity source.
    BIOMETRIC_REFERENCE = "biometric-reference"
    #: Biometric material observed in collected media, never enrolled.
    BIOMETRIC_OBSERVED = "biometric-observed"


@dataclass(frozen=True, slots=True)
class ObjectLocation:
    """Where an object lives: a bucket and a key within it.

    Validated on construction, so an invalid address cannot reach a backend.
    """

    bucket: str
    key: str

    def __post_init__(self) -> None:
        """Reject bucket and key forms that no backend should be asked to store."""
        if not _BUCKET_RE.match(self.bucket):
            raise BlobStoreError(
                f"invalid bucket name {self.bucket!r}: expected 3-63 lowercase "
                "alphanumeric characters, dots or hyphens"
            )
        if not self.key or len(self.key) > MAX_KEY_LENGTH:
            raise BlobStoreError(
                f"object keys must be 1-{MAX_KEY_LENGTH} characters, got {len(self.key)}"
            )
        segments = self.key.split("/")
        if not all(_KEY_SEGMENT_RE.match(segment) for segment in segments):
            raise BlobStoreError(
                f"invalid object key {self.key!r}: each '/'-separated segment must be "
                "non-empty and contain only letters, digits, '.', '_' or '-'"
            )
        if any(segment in {".", ".."} for segment in segments):
            raise BlobStoreError(f"invalid object key {self.key!r}: path traversal is refused")

    def __str__(self) -> str:
        """Render as ``bucket/key`` for logs and error messages."""
        return f"{self.bucket}/{self.key}"


@dataclass(frozen=True, slots=True)
class StoredObject:
    """What a backend knows about an object it holds."""

    location: ObjectLocation
    size_bytes: int
    content_type: str
    modified_at: datetime


@runtime_checkable
class BlobStore(Protocol):
    """Storage of immutable bytes at a bucket/key address.

    Implementations are expected to be safe to call concurrently and to treat
    ``put`` of identical bytes at the same location as a no-op rather than an
    error: ingestion is content-addressed, so repeats are normal.
    """

    @property
    def provider(self) -> str:
        """Short provider identifier, e.g. ``filesystem`` or ``s3``."""
        ...

    async def ping(self) -> None:
        """Raise if the store is not reachable and writable."""
        ...

    async def ensure_bucket(self, bucket: str) -> None:
        """Create ``bucket`` if it does not exist. Idempotent."""
        ...

    async def put(self, location: ObjectLocation, data: bytes, *, content_type: str) -> None:
        """Store ``data`` at ``location``."""
        ...

    async def get(self, location: ObjectLocation) -> bytes | None:
        """Return the stored bytes, or None if nothing is there."""
        ...

    async def get_range(self, location: ObjectLocation, start: int, length: int) -> bytes | None:
        """Return ``length`` bytes from ``start``, or None if nothing is there.

        Exists so a large object can be served to a browser without the API
        holding all of it in memory.
        """
        ...

    async def stat(self, location: ObjectLocation) -> StoredObject | None:
        """Return what is known about the object, or None if absent."""
        ...

    async def delete(self, location: ObjectLocation) -> bool:
        """Remove the object. Returns whether anything was removed."""
        ...

    async def list_keys(
        self, bucket: str, *, prefix: str = "", older_than: datetime | None = None
    ) -> list[str]:
        """Return keys in ``bucket``, optionally filtered by prefix and age.

        The age filter exists for reconciliation: an object written moments ago
        may belong to a request that has not yet committed the row naming it,
        so sweeping recent objects would race live work.
        """
        ...
