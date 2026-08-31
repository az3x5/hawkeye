"""Media ingestion: deduplication, provenance, holds and erasure."""

from __future__ import annotations

import struct
import zlib
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.connectors.filesystem import FilesystemBlobStore
from app.domain.audit import AuditEvent
from app.domain.auth import Principal, Scope
from app.domain.content_types import IMAGE_FORMATS, MediaTooLargeError, MediaType
from app.domain.media import (
    AssetDerivative,
    AssetSource,
    AssetStatus,
    Classification,
    MediaAsset,
    MediaError,
    RetentionHold,
    RetentionHoldError,
    SourceType,
)
from app.services.media import BUCKETS, IngestRequest, MediaService, sha256_bytes, storage_key


def png(width: int = 8, height: int = 8, salt: bytes = b"") -> bytes:
    ihdr = struct.pack(">II", width, height) + b"\x08\x02\x00\x00\x00"
    chunk = struct.pack(">I", len(ihdr)) + b"IHDR" + ihdr
    chunk += struct.pack(">I", zlib.crc32(b"IHDR" + ihdr))
    return b"\x89PNG\r\n\x1a\n" + chunk + salt


class InMemoryMediaRepository:
    """A ``MediaRepository`` that keeps everything in dictionaries."""

    def __init__(self) -> None:
        self.assets: dict[UUID, MediaAsset] = {}
        self.sources: list[AssetSource] = []
        self.derivatives: list[AssetDerivative] = []
        self.holds: dict[UUID, RetentionHold] = {}

    async def add(self, asset: MediaAsset) -> MediaAsset:
        if any(a.sha256 == asset.sha256 for a in self.assets.values()):
            raise MediaError(f"media with hash {asset.sha256} is already held")
        self.assets[asset.media_uuid] = asset
        return asset

    async def get(self, media_uuid: UUID) -> MediaAsset | None:
        return self.assets.get(media_uuid)

    async def find_by_hash(self, sha256: str) -> MediaAsset | None:
        return next((a for a in self.assets.values() if a.sha256 == sha256), None)

    async def list_assets(
        self,
        *,
        media_type: MediaType | None = None,
        classification: Classification | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[MediaAsset]:
        found = [
            a
            for a in self.assets.values()
            if (media_type is None or a.media_type is media_type)
            and (classification is None or a.classification is classification)
        ]
        found.sort(key=lambda a: a.created_at, reverse=True)
        return found[offset : offset + limit]

    async def count_assets(
        self,
        *,
        media_type: MediaType | None = None,
        classification: Classification | None = None,
    ) -> int:
        return len(
            await self.list_assets(
                media_type=media_type, classification=classification, limit=10_000, offset=0
            )
        )

    async def mark_erased(self, media_uuid: UUID, *, erased_at: datetime) -> bool:
        asset = self.assets.get(media_uuid)
        if asset is None or asset.status is AssetStatus.ERASED:
            return False
        from dataclasses import replace

        self.assets[media_uuid] = replace(asset, status=AssetStatus.ERASED, erased_at=erased_at)
        return True

    async def add_source(self, source: AssetSource) -> AssetSource:
        key = (
            source.media_uuid,
            source.source_type,
            source.source_system,
            source.external_source_id,
        )
        if source.external_source_id is not None and any(
            (s.media_uuid, s.source_type, s.source_system, s.external_source_id) == key
            for s in self.sources
        ):
            return source
        self.sources.append(source)
        return source

    async def list_sources(self, media_uuid: UUID) -> Sequence[AssetSource]:
        return [s for s in self.sources if s.media_uuid == media_uuid]

    async def add_derivative(self, derivative: AssetDerivative) -> AssetDerivative:
        self.derivatives.append(derivative)
        return derivative

    async def list_derivatives(self, media_uuid: UUID) -> Sequence[AssetDerivative]:
        return [d for d in self.derivatives if d.source_media_uuid == media_uuid]

    async def add_hold(self, hold: RetentionHold) -> RetentionHold:
        if hold.media_uuid not in self.assets:
            raise MediaError(f"no media asset {hold.media_uuid} to place a hold on")
        self.holds[hold.hold_uuid] = hold
        return hold

    async def release_hold(
        self, hold_uuid: UUID, *, released_by: str, released_at: datetime
    ) -> bool:
        from dataclasses import replace

        hold = self.holds.get(hold_uuid)
        if hold is None or not hold.active:
            return False
        self.holds[hold_uuid] = replace(hold, released_at=released_at, released_by=released_by)
        return True

    async def active_holds(self, media_uuid: UUID) -> Sequence[RetentionHold]:
        return [h for h in self.holds.values() if h.media_uuid == media_uuid and h.active]


class RecordingAuditLog:
    """Captures what the service audits."""

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def record(self, event: AuditEvent) -> AuditEvent:
        self.events.append(event)
        return event

    async def list_events(self, **_: object) -> Sequence[AuditEvent]:
        return self.events

    async def for_person(self, person_uuid: UUID, *, limit: int = 100) -> Sequence[AuditEvent]:
        return [e for e in self.events if e.person_uuid == person_uuid][:limit]

    async def for_identification(self, identification_uuid: UUID) -> Sequence[AuditEvent]:
        return [e for e in self.events if e.identification_uuid == identification_uuid]


@pytest.fixture
def repository() -> InMemoryMediaRepository:
    return InMemoryMediaRepository()


@pytest.fixture
def audit() -> RecordingAuditLog:
    return RecordingAuditLog()


@pytest.fixture
def blobs(tmp_path: Path) -> FilesystemBlobStore:
    return FilesystemBlobStore(tmp_path / "buckets")


@pytest.fixture
def service(
    repository: InMemoryMediaRepository, blobs: FilesystemBlobStore, audit: RecordingAuditLog
) -> MediaService:
    return MediaService(repository=repository, blobs=blobs, audit=audit, max_bytes=1024 * 1024)


@pytest.fixture
def principal() -> Principal:
    return Principal(
        token_uuid=uuid4(),
        subject="operator@example.com",
        kind="user",
        scopes=frozenset({Scope.MEDIA_WRITE, Scope.MEDIA_READ, Scope.ADMIN}),
    )


def request_for(data: bytes, **overrides: object) -> IngestRequest:
    fields: dict[str, object] = {
        "data": data,
        "source_type": SourceType.UPLOAD,
        "source_system": "console",
    }
    fields.update(overrides)
    return IngestRequest(**fields)  # type: ignore[arg-type]


class TestIngestion:
    async def test_media_is_stored_and_described_from_its_bytes(
        self, service: MediaService, blobs: FilesystemBlobStore
    ) -> None:
        data = png(64, 32)
        result = await service.ingest(request_for(data))

        assert result.created is True
        assert result.asset.media_type is MediaType.IMAGE
        assert result.asset.mime_type == "image/png"
        assert (result.asset.width, result.asset.height) == (64, 32)
        assert result.asset.byte_size == len(data)
        assert result.asset.sha256 == sha256_bytes(data)
        assert await blobs.get(result.asset.location) == data

    async def test_the_object_lands_in_the_domain_bucket(self, service: MediaService) -> None:
        result = await service.ingest(request_for(png()))
        assert result.asset.storage_bucket == BUCKETS[result.asset.storage_domain]
        assert result.asset.storage_key == storage_key(result.asset.sha256, "png")

    async def test_identical_bytes_converge_on_one_asset(
        self, service: MediaService, repository: InMemoryMediaRepository
    ) -> None:
        data = png()
        first = await service.ingest(request_for(data))
        second = await service.ingest(request_for(data))

        assert second.created is False
        assert second.asset.media_uuid == first.asset.media_uuid
        assert len(repository.assets) == 1

    async def test_deduplication_keeps_every_arrival(
        self, service: MediaService, repository: InMemoryMediaRepository
    ) -> None:
        """The bytes are one asset; the fact it arrived twice is two records."""
        data = png()
        first = await service.ingest(
            request_for(data, source_type=SourceType.UPLOAD, source_system="console")
        )
        await service.ingest(
            request_for(
                data,
                source_type=SourceType.BLACKGLASS,
                source_system="blackglass",
                external_source_id="bg-991",
            )
        )
        sources = await repository.list_sources(first.asset.media_uuid)
        assert {s.source_type for s in sources} == {SourceType.UPLOAD, SourceType.BLACKGLASS}

    async def test_the_same_arrival_delivered_twice_does_not_duplicate_provenance(
        self, service: MediaService, repository: InMemoryMediaRepository
    ) -> None:
        data = png()
        arrival = request_for(
            data,
            source_type=SourceType.BLACKGLASS,
            source_system="blackglass",
            external_source_id="bg-1",
        )
        result = await service.ingest(arrival)
        await service.ingest(arrival)
        assert len(await repository.list_sources(result.asset.media_uuid)) == 1

    async def test_the_submitting_principal_is_recorded(self, service: MediaService) -> None:
        result = await service.ingest(request_for(png(), submitted_by="operator@example.com"))
        assert result.source.submitted_by == "operator@example.com"

    async def test_media_above_the_ceiling_is_refused(
        self,
        repository: InMemoryMediaRepository,
        blobs: FilesystemBlobStore,
        audit: RecordingAuditLog,
    ) -> None:
        tiny = MediaService(repository=repository, blobs=blobs, audit=audit, max_bytes=32)
        with pytest.raises(MediaTooLargeError, match="ceiling"):
            await tiny.ingest(request_for(png(8, 8, salt=b"x" * 200)))

    async def test_a_format_outside_the_allowlist_is_refused(self, service: MediaService) -> None:
        from app.domain.content_types import UnsupportedMediaError

        with pytest.raises(UnsupportedMediaError):
            await service.ingest(
                request_for(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n", allowed_formats=IMAGE_FORMATS)
            )

    async def test_nothing_is_stored_when_the_media_is_refused(
        self, service: MediaService, repository: InMemoryMediaRepository, blobs: FilesystemBlobStore
    ) -> None:
        from app.domain.content_types import UnsupportedMediaError

        with pytest.raises(UnsupportedMediaError):
            await service.ingest(request_for(b"not media at all, just some prose"))
        assert repository.assets == {}
        assert await blobs.list_keys(BUCKETS[list(BUCKETS)[0]]) == []


class TestRetrieval:
    async def test_content_returns_the_stored_bytes(self, service: MediaService) -> None:
        data = png(16, 16)
        result = await service.ingest(request_for(data))
        found = await service.content(result.asset.media_uuid)
        assert found is not None
        assert found[1] == data

    async def test_a_range_read_returns_only_that_window(self, service: MediaService) -> None:
        data = png(16, 16)
        result = await service.ingest(request_for(data))
        found = await service.content_range(result.asset.media_uuid, 0, 8)
        assert found is not None
        assert found[1] == data[:8]

    async def test_content_of_an_unknown_asset_is_none(self, service: MediaService) -> None:
        assert await service.content(uuid4()) is None

    async def test_listing_is_capped_at_the_server_ceiling(
        self,
        repository: InMemoryMediaRepository,
        blobs: FilesystemBlobStore,
        audit: RecordingAuditLog,
    ) -> None:
        service = MediaService(
            repository=repository,
            blobs=blobs,
            audit=audit,
            max_bytes=1024 * 1024,
            page_size_limit=2,
        )
        for index in range(5):
            await service.ingest(request_for(png(8, 8, salt=bytes([index]))))
        items, total = await service.list_assets(limit=1000, offset=0)
        assert len(items) == 2
        assert total == 5


class TestAuditAndSensitiveReads:
    async def test_reading_biometric_media_is_audited(
        self, service: MediaService, audit: RecordingAuditLog, principal: Principal
    ) -> None:
        result = await service.ingest(request_for(png(), classification=Classification.BIOMETRIC))
        await service.record_view(result.asset, principal)
        assert [e.action.value for e in audit.events] == ["media_viewed"]

    async def test_reading_ordinary_media_is_not_audited(
        self, service: MediaService, audit: RecordingAuditLog, principal: Principal
    ) -> None:
        """Auditing every thumbnail buries the reads that matter."""
        result = await service.ingest(request_for(png(), classification=Classification.INTERNAL))
        await service.record_view(result.asset, principal)
        assert audit.events == []

    async def test_the_audit_record_never_carries_the_bytes(
        self, service: MediaService, audit: RecordingAuditLog, principal: Principal
    ) -> None:
        result = await service.ingest(request_for(png(), classification=Classification.BIOMETRIC))
        await service.erase(result.asset.media_uuid, principal, reason="subject request")
        recorded = " ".join(str(e.details) for e in audit.events)
        assert "\\x89PNG" not in recorded
        assert result.asset.sha256 in recorded


class TestErasureAndHolds:
    async def test_erasure_removes_the_bytes_but_keeps_the_metadata(
        self, service: MediaService, blobs: FilesystemBlobStore, principal: Principal
    ) -> None:
        result = await service.ingest(request_for(png()))
        assert await service.erase(result.asset.media_uuid, principal, reason="expired") is True

        assert await blobs.get(result.asset.location) is None
        remaining = await service.get(result.asset.media_uuid)
        assert remaining is not None
        assert remaining.status is AssetStatus.ERASED
        assert remaining.erased_at is not None
        # The hash outlives the bytes, so a past decision citing it stays explicable.
        assert remaining.sha256 == result.asset.sha256

    async def test_an_erased_asset_no_longer_serves_content(
        self, service: MediaService, principal: Principal
    ) -> None:
        result = await service.ingest(request_for(png()))
        await service.erase(result.asset.media_uuid, principal, reason="expired")
        assert await service.content(result.asset.media_uuid) is None

    async def test_erasure_is_audited(
        self, service: MediaService, audit: RecordingAuditLog, principal: Principal
    ) -> None:
        result = await service.ingest(request_for(png()))
        await service.erase(result.asset.media_uuid, principal, reason="subject request")
        event = audit.events[-1]
        assert event.action.value == "media_erased"
        assert event.actor.identifier == "operator@example.com"
        assert event.details["reason"] == "subject request"

    async def test_erasing_an_unknown_asset_reports_false(
        self, service: MediaService, principal: Principal
    ) -> None:
        assert await service.erase(uuid4(), principal, reason="none") is False

    async def test_a_retention_hold_blocks_erasure(
        self, service: MediaService, blobs: FilesystemBlobStore, principal: Principal
    ) -> None:
        result = await service.ingest(request_for(png()))
        await service.place_hold(result.asset.media_uuid, principal, reason="litigation")

        with pytest.raises(RetentionHoldError, match="retention hold"):
            await service.erase(result.asset.media_uuid, principal, reason="routine cleanup")
        # The veto is real: the bytes are still there.
        assert await blobs.get(result.asset.location) is not None

    async def test_releasing_the_hold_allows_erasure_again(
        self, service: MediaService, principal: Principal
    ) -> None:
        result = await service.ingest(request_for(png()))
        hold = await service.place_hold(result.asset.media_uuid, principal, reason="litigation")
        assert await service.release_hold(hold.hold_uuid, principal) is True
        assert await service.erase(result.asset.media_uuid, principal, reason="cleared") is True

    async def test_releasing_an_already_released_hold_reports_false(
        self, service: MediaService, principal: Principal
    ) -> None:
        result = await service.ingest(request_for(png()))
        hold = await service.place_hold(result.asset.media_uuid, principal, reason="litigation")
        await service.release_hold(hold.hold_uuid, principal)
        assert await service.release_hold(hold.hold_uuid, principal) is False

    async def test_a_hold_on_unknown_media_is_refused(
        self, service: MediaService, principal: Principal
    ) -> None:
        with pytest.raises(MediaError):
            await service.place_hold(uuid4(), principal, reason="litigation")

    async def test_placing_and_releasing_holds_is_audited(
        self, service: MediaService, audit: RecordingAuditLog, principal: Principal
    ) -> None:
        result = await service.ingest(request_for(png()))
        hold = await service.place_hold(result.asset.media_uuid, principal, reason="litigation")
        await service.release_hold(hold.hold_uuid, principal)
        assert [e.action.value for e in audit.events] == [
            "media_hold_placed",
            "media_hold_released",
        ]


class TestDomainInvariants:
    def test_an_asset_cannot_be_derived_from_itself(self) -> None:
        same = uuid4()
        with pytest.raises(MediaError, match="derived from itself"):
            AssetDerivative(
                source_media_uuid=same,
                derived_media_uuid=same,
                transform="thumbnail",
                transform_version="1",
            )

    def test_a_hold_must_say_why_it_exists(self) -> None:
        with pytest.raises(MediaError, match="why it was placed"):
            RetentionHold(media_uuid=uuid4(), reason="   ", placed_by="admin")

    def test_an_asset_hash_must_be_a_sha256(self) -> None:
        with pytest.raises(MediaError, match="64 lowercase hex"):
            MediaAsset(
                sha256="not-a-hash",
                byte_size=1,
                media_type=MediaType.IMAGE,
                mime_type="image/png",
                storage_domain=list(BUCKETS)[0],
                storage_bucket="eagleeye-media",
                storage_key="ab/x.png",
            )

    def test_a_source_must_name_its_system(self) -> None:
        with pytest.raises(MediaError, match="name the system"):
            AssetSource(media_uuid=uuid4(), source_type=SourceType.UPLOAD, source_system="  ")
