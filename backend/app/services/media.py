"""Media ingestion, retrieval and erasure.

The ordering in ``ingest`` matters and is not arbitrary. Bytes are written to
object storage *before* the row that names them, so a committed row never
points at an object the worker cannot read. The reverse order would produce
dangling metadata on a crash, which is the harder failure to detect: a missing
object looks like corruption, while an orphaned object is found by the
reconciliation sweep and costs only disk until then.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from app.domain.audit import AuditAction, AuditEvent, AuditLog
from app.domain.auth import Principal
from app.domain.content_types import (
    MediaFormat,
    MediaTooLargeError,
    MediaType,
    UnsupportedMediaError,
    sniff,
    verify_declared,
)
from app.domain.media import (
    AssetSource,
    Classification,
    MediaAsset,
    MediaError,
    MediaRepository,
    RetentionHold,
    RetentionHoldError,
    SourceType,
)
from app.domain.storage import BlobStore, ObjectLocation, StorageDomain

logger = logging.getLogger(__name__)

#: Bucket per storage domain. Separate buckets so a deployment can grant a
#: worker read access to general media without also granting it reference
#: biometrics — a policy that a single bucket cannot express.
BUCKETS: dict[StorageDomain, str] = {
    StorageDomain.MEDIA: "eagleeye-media",
    StorageDomain.DERIVED: "eagleeye-derived",
    StorageDomain.BIOMETRIC_REFERENCE: "eagleeye-face-reference",
    StorageDomain.BIOMETRIC_OBSERVED: "eagleeye-face-observations",
}

#: Sharded by the first two hex characters of the digest. Object stores do not
#: need this the way a filesystem does, but keeping one layout across both
#: backends means a migration is a copy rather than a re-addressing.
_SHARD_CHARS = 2


def sha256_bytes(data: bytes) -> str:
    """Return the SHA-256 of ``data`` as lowercase hex."""
    return hashlib.sha256(data).hexdigest()


def storage_key(digest: str, extension: str) -> str:
    """Build the object key for a digest.

    The extension is cosmetic — the content type is stored as metadata and the
    digest is the real address — but it makes an operator browsing a bucket
    able to tell a video from a document.
    """
    return f"{digest[:_SHARD_CHARS]}/{digest}.{extension}"


@dataclass(frozen=True, slots=True)
class IngestRequest:
    """One submission of media bytes with its provenance."""

    data: bytes
    source_type: SourceType
    source_system: str
    declared_content_type: str | None = None
    classification: Classification = Classification.INTERNAL
    storage_domain: StorageDomain = StorageDomain.MEDIA
    external_source_id: str | None = None
    source_url: str | None = None
    collected_at: datetime | None = None
    published_at: datetime | None = None
    collector_version: str | None = None
    submitted_by: str | None = None
    #: Restricts the accepted formats. None accepts every recognised format.
    allowed_formats: frozenset[MediaFormat] | None = None


@dataclass(frozen=True, slots=True)
class IngestResult:
    """Outcome of an ingestion."""

    asset: MediaAsset
    source: AssetSource
    #: False when these bytes were already held. Not an error: the provenance
    #: record is still added, so the second arrival is not lost.
    created: bool


class MediaService:
    """Ingests, serves and erases media assets."""

    def __init__(
        self,
        *,
        repository: MediaRepository,
        blobs: BlobStore,
        audit: AuditLog,
        max_bytes: int,
        page_size_limit: int = 200,
    ) -> None:
        """Wire the service to its collaborators."""
        self._repository = repository
        self._blobs = blobs
        self._audit = audit
        self._max_bytes = max_bytes
        self._page_size_limit = page_size_limit

    @property
    def max_bytes(self) -> int:
        """Largest single upload this service accepts."""
        return self._max_bytes

    @property
    def page_size_limit(self) -> int:
        """Server ceiling on a listing page, applied regardless of what is asked."""
        return self._page_size_limit

    async def ingest(self, request: IngestRequest) -> IngestResult:
        """Store media and record where it came from.

        Idempotent on content: the same bytes converge on one asset. Every
        arrival is recorded regardless, because deduplicating the bytes must
        not deduplicate the fact that somebody sent them again.
        """
        if not request.data:
            raise UnsupportedMediaError("the media is empty")
        if len(request.data) > self._max_bytes:
            raise MediaTooLargeError(
                f"media is {len(request.data)} bytes, above the {self._max_bytes} byte ceiling"
            )

        detected = sniff(request.data, allowed=request.allowed_formats)
        verify_declared(request.declared_content_type, detected.format)

        digest = sha256_bytes(request.data)
        existing = await self._repository.find_by_hash(digest)
        if existing is not None:
            source = await self._record_source(existing.media_uuid, request)
            logger.info(
                "media already held; recorded an additional source",
                extra={"media_uuid": str(existing.media_uuid), "sha256": digest},
            )
            return IngestResult(asset=existing, source=source, created=False)

        bucket = BUCKETS[request.storage_domain]
        location = ObjectLocation(bucket=bucket, key=storage_key(digest, detected.format.extension))
        await self._blobs.put(location, request.data, content_type=detected.format.mime_type)

        asset = MediaAsset(
            sha256=digest,
            byte_size=len(request.data),
            media_type=detected.format.media_type,
            mime_type=detected.format.mime_type,
            classification=request.classification,
            storage_domain=request.storage_domain,
            storage_bucket=location.bucket,
            storage_key=location.key,
            width=detected.dimensions.width if detected.dimensions else None,
            height=detected.dimensions.height if detected.dimensions else None,
        )
        try:
            await self._repository.add(asset)
        except MediaError:
            # Lost a race with a concurrent identical ingestion. The bytes are
            # content-addressed, so the winner's object is ours too; converge
            # on their asset rather than failing a caller who did nothing wrong.
            won = await self._repository.find_by_hash(digest)
            if won is None:
                raise
            source = await self._record_source(won.media_uuid, request)
            return IngestResult(asset=won, source=source, created=False)

        source = await self._record_source(asset.media_uuid, request)
        logger.info(
            "media ingested",
            extra={
                "media_uuid": str(asset.media_uuid),
                "media_type": asset.media_type.value,
                "byte_size": asset.byte_size,
                "source_type": request.source_type.value,
            },
        )
        return IngestResult(asset=asset, source=source, created=True)

    async def _record_source(self, media_uuid: UUID, request: IngestRequest) -> AssetSource:
        source = AssetSource(
            media_uuid=media_uuid,
            source_type=request.source_type,
            source_system=request.source_system,
            external_source_id=request.external_source_id,
            source_url=request.source_url,
            collected_at=request.collected_at,
            published_at=request.published_at,
            collector_version=request.collector_version,
            submitted_by=request.submitted_by,
        )
        return await self._repository.add_source(source)

    async def get(self, media_uuid: UUID) -> MediaAsset | None:
        """Return one asset's metadata, erased assets included.

        An erased asset still answers: its metadata is what makes a past
        decision explicable once the material behind it is gone.
        """
        return await self._repository.get(media_uuid)

    async def list_assets(
        self,
        *,
        media_type: MediaType | None = None,
        classification: Classification | None = None,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[MediaAsset], int]:
        """Return a page of assets and the total matching the filters."""
        capped = min(limit, self._page_size_limit)
        assets = await self._repository.list_assets(
            media_type=media_type,
            classification=classification,
            limit=capped,
            offset=offset,
        )
        total = await self._repository.count_assets(
            media_type=media_type, classification=classification
        )
        return assets, total

    async def list_sources(self, media_uuid: UUID) -> Sequence[AssetSource]:
        """Return every recorded arrival of this asset, oldest first."""
        return await self._repository.list_sources(media_uuid)

    async def active_holds(self, media_uuid: UUID) -> Sequence[RetentionHold]:
        """Return the holds currently blocking erasure of this asset."""
        return await self._repository.active_holds(media_uuid)

    async def record_view(self, asset: MediaAsset, principal: Principal) -> None:
        """Audit a read of sensitive material.

        Only restricted and biometric assets are logged. Auditing every
        thumbnail fetch would bury the reads that matter under the ones that
        do not, which makes the log less useful, not more.
        """
        if asset.classification not in {Classification.RESTRICTED, Classification.BIOMETRIC}:
            return
        await self._audit.record(
            AuditEvent(
                action=AuditAction.MEDIA_VIEWED,
                actor=principal.as_actor(),
                details={
                    "media_uuid": str(asset.media_uuid),
                    "classification": asset.classification.value,
                    "media_type": asset.media_type.value,
                },
            )
        )

    async def content(self, media_uuid: UUID) -> tuple[MediaAsset, bytes] | None:
        """Return an asset and its bytes, or None if either is unavailable."""
        asset = await self._repository.get(media_uuid)
        if asset is None or not asset.readable:
            return None
        data = await self._blobs.get(asset.location)
        return None if data is None else (asset, data)

    async def content_range(
        self, media_uuid: UUID, start: int, length: int
    ) -> tuple[MediaAsset, bytes] | None:
        """Return part of an asset's bytes, for range requests over large media."""
        asset = await self._repository.get(media_uuid)
        if asset is None or not asset.readable:
            return None
        data = await self._blobs.get_range(asset.location, start, length)
        return None if data is None else (asset, data)

    async def erase(self, media_uuid: UUID, principal: Principal, *, reason: str) -> bool:
        """Erase an asset's bytes, keeping its metadata for audit.

        Refuses while any retention hold is active. The metadata survives on
        purpose: a decision that cited this media stays explicable after the
        biometric or personal material behind it is gone.
        """
        asset = await self._repository.get(media_uuid)
        if asset is None:
            return False

        holds = await self._repository.active_holds(media_uuid)
        if holds:
            raise RetentionHoldError(
                f"media {media_uuid} is under {len(holds)} active retention hold(s) "
                "and cannot be erased until they are released"
            )

        await self._blobs.delete(asset.location)
        erased_at = datetime.now(UTC)
        changed = await self._repository.mark_erased(media_uuid, erased_at=erased_at)
        await self._audit.record(
            AuditEvent(
                action=AuditAction.MEDIA_ERASED,
                actor=principal.as_actor(),
                occurred_at=erased_at,
                details={
                    "media_uuid": str(media_uuid),
                    "sha256": asset.sha256,
                    "media_type": asset.media_type.value,
                    "classification": asset.classification.value,
                    "reason": reason,
                },
            )
        )
        return changed

    async def place_hold(
        self, media_uuid: UUID, principal: Principal, *, reason: str
    ) -> RetentionHold:
        """Place a retention hold that blocks erasure."""
        asset = await self._repository.get(media_uuid)
        if asset is None:
            raise MediaError(f"no media asset {media_uuid}")
        hold = await self._repository.add_hold(
            RetentionHold(media_uuid=media_uuid, reason=reason, placed_by=principal.subject)
        )
        await self._audit.record(
            AuditEvent(
                action=AuditAction.MEDIA_HOLD_PLACED,
                actor=principal.as_actor(),
                details={
                    "media_uuid": str(media_uuid),
                    "hold_uuid": str(hold.hold_uuid),
                    "reason": reason,
                },
            )
        )
        return hold

    async def release_hold(self, hold_uuid: UUID, principal: Principal) -> bool:
        """Release a retention hold. Returns whether an active hold was released."""
        released = await self._repository.release_hold(
            hold_uuid, released_by=principal.subject, released_at=datetime.now(UTC)
        )
        if released:
            await self._audit.record(
                AuditEvent(
                    action=AuditAction.MEDIA_HOLD_RELEASED,
                    actor=principal.as_actor(),
                    details={"hold_uuid": str(hold_uuid)},
                )
            )
        return released
