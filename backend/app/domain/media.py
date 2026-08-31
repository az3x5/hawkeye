"""Media as a first-class asset.

Until now an image was a SHA-256 mentioned by a face sample or an
identification. That works for one modality and one owner, but it cannot say
what a file *is*, where it came from, who may see it, or what was computed
from it — all of which a multimodal platform has to answer.

Three ideas carry the model:

**One asset, many sources.** The same photograph reaches us from an upload, a
BlackGlass record and a news article. Deduplicating on the hash must not
collapse the fact that it arrived three times from three places, so
provenance is a separate row per arrival and never overwritten.

**Derivation is recorded, not implied.** A thumbnail, a face crop and an
extracted audio track are themselves assets, linked to the asset and the
transform that produced them. Reprocessing adds lineage instead of replacing
it.

**Deletion is a decision with a veto.** A retention hold blocks erasure of
material somebody has said must be kept, so "delete everything for this
person" cannot quietly destroy evidence that is under hold.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import UUID, uuid4

from app.domain.content_types import MediaType
from app.domain.storage import ObjectLocation, StorageDomain


class MediaError(Exception):
    """A media operation could not be carried out."""


class RetentionHoldError(MediaError):
    """The operation is blocked by a retention hold."""


class AssetStatus(StrEnum):
    """Where an asset is in its lifecycle."""

    #: Bytes are stored and the metadata is committed.
    STORED = "stored"
    #: Held but not to be processed or served pending a decision.
    QUARANTINED = "quarantined"
    #: Metadata retained for audit; bytes have been erased.
    ERASED = "erased"


class Classification(StrEnum):
    """How sensitive an asset is.

    Authorisation consults this, so it is deliberately coarse and ordered.
    ``BIOMETRIC`` is separate from ``RESTRICTED`` rather than more severe: it
    is a different kind of sensitivity with its own legal basis, not simply a
    higher grade of the same one.
    """

    PUBLIC = "public"
    INTERNAL = "internal"
    RESTRICTED = "restricted"
    BIOMETRIC = "biometric"


class SourceType(StrEnum):
    """Where an asset came from.

    Provenance drives trust: material from the identity server is
    authoritative in a way a social post never is, and the difference has to
    survive into every downstream claim.
    """

    UPLOAD = "upload"
    IDENTITY_SERVER = "identity_server"
    BLACKGLASS = "blackglass"
    NEWS = "news"
    SOCIAL = "social"
    CAMERA = "camera"
    DOCUMENT = "document"
    API = "api"
    OTHER = "other"


#: Source types whose material is authoritative enough to be enrolled as a
#: reference biometric. Everything else can only ever be an observation.
REFERENCE_CAPABLE_SOURCES: frozenset[SourceType] = frozenset(
    {SourceType.IDENTITY_SERVER, SourceType.UPLOAD}
)


@dataclass(frozen=True, slots=True)
class MediaAsset:
    """One immutable piece of media.

    ``sha256`` is unique across the table: identical bytes are one asset, no
    matter how many times or from how many places they arrive.
    """

    sha256: str
    byte_size: int
    media_type: MediaType
    mime_type: str
    storage_domain: StorageDomain
    storage_bucket: str
    storage_key: str
    media_uuid: UUID = field(default_factory=uuid4)
    classification: Classification = Classification.INTERNAL
    status: AssetStatus = AssetStatus.STORED
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    erased_at: datetime | None = None

    def __post_init__(self) -> None:
        """Validate the invariants storage and authorisation depend on."""
        if len(self.sha256) != 64 or not all(c in "0123456789abcdef" for c in self.sha256):
            raise MediaError(f"sha256 must be 64 lowercase hex characters, got {self.sha256!r}")
        if self.byte_size <= 0:
            raise MediaError(f"byte_size must be positive, got {self.byte_size}")
        for name, value in (("width", self.width), ("height", self.height)):
            if value is not None and value <= 0:
                raise MediaError(f"{name} must be positive when known, got {value}")
        if self.duration_ms is not None and self.duration_ms < 0:
            raise MediaError(f"duration_ms cannot be negative, got {self.duration_ms}")

    @property
    def location(self) -> ObjectLocation:
        """Where the bytes live."""
        return ObjectLocation(bucket=self.storage_bucket, key=self.storage_key)

    @property
    def readable(self) -> bool:
        """Whether the bytes may still be served."""
        return self.status is AssetStatus.STORED


@dataclass(frozen=True, slots=True)
class AssetSource:
    """One arrival of an asset: where it came from and when.

    An asset accumulates these. Deduplication adds a source; it never removes
    one, because losing an arrival loses the reason a later claim was made.
    """

    media_uuid: UUID
    source_type: SourceType
    source_system: str
    source_uuid: UUID = field(default_factory=uuid4)
    external_source_id: str | None = None
    source_url: str | None = None
    collected_at: datetime | None = None
    published_at: datetime | None = None
    ingested_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    collector_version: str | None = None
    submitted_by: str | None = None

    def __post_init__(self) -> None:
        """Validate that the source names a system."""
        if not self.source_system.strip():
            raise MediaError("an asset source must name the system it came from")


@dataclass(frozen=True, slots=True)
class AssetDerivative:
    """An asset computed from another asset.

    Both sides are assets, so a derivative of a derivative — a face crop taken
    from a video keyframe — is expressible without a special case.
    """

    source_media_uuid: UUID
    derived_media_uuid: UUID
    transform: str
    transform_version: str
    derivative_uuid: UUID = field(default_factory=uuid4)
    #: The analysis run that produced it, once M12 exists to record runs.
    analysis_uuid: UUID | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        """Reject a derivative of itself and an unnamed transform."""
        if self.source_media_uuid == self.derived_media_uuid:
            raise MediaError("an asset cannot be derived from itself")
        if not self.transform.strip():
            raise MediaError("a derivative must name the transform that produced it")
        if not self.transform_version.strip():
            raise MediaError("a derivative must name the transform version")


@dataclass(frozen=True, slots=True)
class RetentionHold:
    """A standing instruction that an asset must not be erased."""

    media_uuid: UUID
    reason: str
    placed_by: str
    hold_uuid: UUID = field(default_factory=uuid4)
    placed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    released_at: datetime | None = None
    released_by: str | None = None

    def __post_init__(self) -> None:
        """Require a hold to say why it exists and who placed it."""
        if not self.reason.strip():
            raise MediaError("a retention hold must record why it was placed")
        if not self.placed_by.strip():
            raise MediaError("a retention hold must record who placed it")

    @property
    def active(self) -> bool:
        """Whether this hold still blocks erasure."""
        return self.released_at is None


@runtime_checkable
class MediaRepository(Protocol):
    """Persistence of media assets and their provenance."""

    async def add(self, asset: MediaAsset) -> MediaAsset:
        """Insert ``asset``. Raises ``MediaError`` if its hash is already held."""
        ...

    async def get(self, media_uuid: UUID) -> MediaAsset | None:
        """Return the asset, or None."""
        ...

    async def find_by_hash(self, sha256: str) -> MediaAsset | None:
        """Return the asset with these exact bytes, or None."""
        ...

    async def list_assets(
        self,
        *,
        media_type: MediaType | None = None,
        classification: Classification | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[MediaAsset]:
        """Return assets newest first, filtered as asked."""
        ...

    async def count_assets(
        self,
        *,
        media_type: MediaType | None = None,
        classification: Classification | None = None,
    ) -> int:
        """Return how many assets match the filters."""
        ...

    async def mark_erased(self, media_uuid: UUID, *, erased_at: datetime) -> bool:
        """Record that the bytes are gone, keeping the metadata. Returns whether it changed."""
        ...

    async def add_source(self, source: AssetSource) -> AssetSource:
        """Attach a provenance record. Repeating an identical arrival is a no-op."""
        ...

    async def list_sources(self, media_uuid: UUID) -> Sequence[AssetSource]:
        """Return every recorded arrival of this asset, oldest first."""
        ...

    async def add_derivative(self, derivative: AssetDerivative) -> AssetDerivative:
        """Record that one asset was computed from another."""
        ...

    async def list_derivatives(self, media_uuid: UUID) -> Sequence[AssetDerivative]:
        """Return assets derived from this one."""
        ...

    async def add_hold(self, hold: RetentionHold) -> RetentionHold:
        """Place a retention hold."""
        ...

    async def release_hold(
        self, hold_uuid: UUID, *, released_by: str, released_at: datetime
    ) -> bool:
        """Release a hold. Returns whether an active hold was released."""
        ...

    async def active_holds(self, media_uuid: UUID) -> Sequence[RetentionHold]:
        """Return the holds currently blocking erasure of this asset."""
        ...
