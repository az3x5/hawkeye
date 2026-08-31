"""The media repository against real PostgreSQL.

The in-memory fake used elsewhere cannot prove what actually protects the
data: the unique hash, the provenance constraint, the erasure invariant and
the foreign keys are database behaviour, and only the database can be asked
whether they hold.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.postgres.connector import PostgresConnector
from app.connectors.postgres.media import SqlAlchemyMediaRepository
from app.connectors.postgres.media_tables import (
    media_asset_derivatives,
    media_asset_sources,
    media_assets,
    media_retention_holds,
)
from app.domain.content_types import MediaType
from app.domain.media import (
    AssetDerivative,
    AssetSource,
    AssetStatus,
    Classification,
    MediaAsset,
    MediaError,
    RetentionHold,
    SourceType,
)
from app.domain.storage import StorageDomain

INTEGRATION_DSN = os.environ.get("FACEID_TEST_POSTGRES_DSN")

pytestmark = pytest.mark.skipif(
    INTEGRATION_DSN is None,
    reason="set FACEID_TEST_POSTGRES_DSN to run PostgreSQL integration tests",
)


def asset(sha: str | None = None, **overrides: object) -> MediaAsset:
    digest = sha or uuid4().hex + uuid4().hex
    fields: dict[str, object] = {
        "sha256": digest,
        "byte_size": 1024,
        "media_type": MediaType.IMAGE,
        "mime_type": "image/png",
        "storage_domain": StorageDomain.MEDIA,
        "storage_bucket": "eagleeye-media",
        "storage_key": f"{digest[:2]}/{digest}.png",
    }
    fields.update(overrides)
    return MediaAsset(**fields)  # type: ignore[arg-type]


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    assert INTEGRATION_DSN is not None
    connector = PostgresConnector(INTEGRATION_DSN, echo=False)
    async with connector.session() as opened:
        for table in (
            media_retention_holds,
            media_asset_derivatives,
            media_asset_sources,
            media_assets,
        ):
            await opened.execute(table.delete())
        yield opened
    await connector.close()


@pytest.fixture
def repository(session: AsyncSession) -> SqlAlchemyMediaRepository:
    return SqlAlchemyMediaRepository(session)


class TestAssets:
    async def test_an_asset_round_trips_through_the_database(
        self, repository: SqlAlchemyMediaRepository
    ) -> None:
        stored = await repository.add(asset(width=640, height=480))
        found = await repository.get(stored.media_uuid)
        assert found == stored

    async def test_identical_bytes_cannot_be_stored_twice(
        self, repository: SqlAlchemyMediaRepository
    ) -> None:
        digest = uuid4().hex + uuid4().hex
        await repository.add(asset(digest))
        with pytest.raises(MediaError, match="already held"):
            await repository.add(asset(digest))

    async def test_lookup_by_hash_finds_the_asset(
        self, repository: SqlAlchemyMediaRepository
    ) -> None:
        stored = await repository.add(asset())
        found = await repository.find_by_hash(stored.sha256)
        assert found is not None
        assert found.media_uuid == stored.media_uuid

    async def test_listing_filters_by_type_and_classification(
        self, repository: SqlAlchemyMediaRepository
    ) -> None:
        await repository.add(asset(classification=Classification.BIOMETRIC))
        await repository.add(asset(media_type=MediaType.DOCUMENT, mime_type="application/pdf"))
        images = await repository.list_assets(media_type=MediaType.IMAGE)
        assert [a.media_type for a in images] == [MediaType.IMAGE]
        assert await repository.count_assets(classification=Classification.BIOMETRIC) == 1

    async def test_erasure_keeps_the_row_and_records_when(
        self, repository: SqlAlchemyMediaRepository
    ) -> None:
        stored = await repository.add(asset())
        moment = datetime.now(UTC)
        assert await repository.mark_erased(stored.media_uuid, erased_at=moment) is True

        found = await repository.get(stored.media_uuid)
        assert found is not None
        assert found.status is AssetStatus.ERASED
        assert found.erased_at is not None
        assert found.sha256 == stored.sha256

    async def test_erasing_twice_reports_no_further_change(
        self, repository: SqlAlchemyMediaRepository
    ) -> None:
        stored = await repository.add(asset())
        moment = datetime.now(UTC)
        await repository.mark_erased(stored.media_uuid, erased_at=moment)
        assert await repository.mark_erased(stored.media_uuid, erased_at=moment) is False


class TestProvenance:
    async def test_sources_accumulate_for_one_asset(
        self, repository: SqlAlchemyMediaRepository
    ) -> None:
        stored = await repository.add(asset())
        await repository.add_source(
            AssetSource(
                media_uuid=stored.media_uuid,
                source_type=SourceType.UPLOAD,
                source_system="console",
            )
        )
        await repository.add_source(
            AssetSource(
                media_uuid=stored.media_uuid,
                source_type=SourceType.BLACKGLASS,
                source_system="blackglass",
                external_source_id="bg-1",
            )
        )
        sources = await repository.list_sources(stored.media_uuid)
        assert {s.source_type for s in sources} == {SourceType.UPLOAD, SourceType.BLACKGLASS}

    async def test_the_same_external_arrival_converges(
        self, repository: SqlAlchemyMediaRepository
    ) -> None:
        stored = await repository.add(asset())
        for _ in range(3):
            await repository.add_source(
                AssetSource(
                    media_uuid=stored.media_uuid,
                    source_type=SourceType.BLACKGLASS,
                    source_system="blackglass",
                    external_source_id="bg-77",
                )
            )
        assert len(await repository.list_sources(stored.media_uuid)) == 1

    async def test_a_source_for_unknown_media_is_refused(
        self, repository: SqlAlchemyMediaRepository
    ) -> None:
        with pytest.raises(MediaError):
            await repository.add_source(
                AssetSource(
                    media_uuid=uuid4(),
                    source_type=SourceType.UPLOAD,
                    source_system="console",
                )
            )


class TestLineage:
    async def test_a_derivative_links_two_assets(
        self, repository: SqlAlchemyMediaRepository
    ) -> None:
        source = await repository.add(asset())
        derived = await repository.add(asset())
        await repository.add_derivative(
            AssetDerivative(
                source_media_uuid=source.media_uuid,
                derived_media_uuid=derived.media_uuid,
                transform="thumbnail",
                transform_version="pillow-11.0",
            )
        )
        lineage = await repository.list_derivatives(source.media_uuid)
        assert [d.derived_media_uuid for d in lineage] == [derived.media_uuid]

    async def test_the_same_transform_recorded_twice_converges(
        self, repository: SqlAlchemyMediaRepository
    ) -> None:
        source = await repository.add(asset())
        derived = await repository.add(asset())
        for _ in range(2):
            await repository.add_derivative(
                AssetDerivative(
                    source_media_uuid=source.media_uuid,
                    derived_media_uuid=derived.media_uuid,
                    transform="thumbnail",
                    transform_version="pillow-11.0",
                )
            )
        assert len(await repository.list_derivatives(source.media_uuid)) == 1

    async def test_a_derivative_of_unknown_media_is_refused(
        self, repository: SqlAlchemyMediaRepository
    ) -> None:
        source = await repository.add(asset())
        with pytest.raises(MediaError):
            await repository.add_derivative(
                AssetDerivative(
                    source_media_uuid=source.media_uuid,
                    derived_media_uuid=uuid4(),
                    transform="thumbnail",
                    transform_version="1",
                )
            )


class TestHolds:
    async def test_an_active_hold_is_listed(self, repository: SqlAlchemyMediaRepository) -> None:
        stored = await repository.add(asset())
        await repository.add_hold(
            RetentionHold(
                media_uuid=stored.media_uuid, reason="litigation", placed_by="admin@example.com"
            )
        )
        holds = await repository.active_holds(stored.media_uuid)
        assert [h.reason for h in holds] == ["litigation"]

    async def test_a_released_hold_no_longer_blocks(
        self, repository: SqlAlchemyMediaRepository
    ) -> None:
        stored = await repository.add(asset())
        hold = await repository.add_hold(
            RetentionHold(
                media_uuid=stored.media_uuid, reason="litigation", placed_by="admin@example.com"
            )
        )
        released = await repository.release_hold(
            hold.hold_uuid, released_by="admin@example.com", released_at=datetime.now(UTC)
        )
        assert released is True
        assert await repository.active_holds(stored.media_uuid) == []

    async def test_releasing_an_unknown_hold_reports_false(
        self, repository: SqlAlchemyMediaRepository
    ) -> None:
        assert (
            await repository.release_hold(
                uuid4(), released_by="admin@example.com", released_at=datetime.now(UTC)
            )
            is False
        )

    async def test_a_hold_on_unknown_media_is_refused(
        self, repository: SqlAlchemyMediaRepository
    ) -> None:
        with pytest.raises(MediaError):
            await repository.add_hold(
                RetentionHold(media_uuid=uuid4(), reason="x", placed_by="admin@example.com")
            )
