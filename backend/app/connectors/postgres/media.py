"""Media repository backed by PostgreSQL."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Row, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

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


def _to_asset(row: Row[tuple[Any, ...]]) -> MediaAsset:
    return MediaAsset(
        media_uuid=row.media_uuid,
        sha256=row.sha256,
        byte_size=row.byte_size,
        media_type=MediaType(row.media_type),
        mime_type=row.mime_type,
        classification=Classification(row.classification),
        status=AssetStatus(row.status),
        storage_domain=StorageDomain(row.storage_domain),
        storage_bucket=row.storage_bucket,
        storage_key=row.storage_key,
        width=row.width,
        height=row.height,
        duration_ms=row.duration_ms,
        created_at=row.created_at,
        erased_at=row.erased_at,
    )


def _to_source(row: Row[tuple[Any, ...]]) -> AssetSource:
    return AssetSource(
        source_uuid=row.source_uuid,
        media_uuid=row.media_uuid,
        source_type=SourceType(row.source_type),
        source_system=row.source_system,
        external_source_id=row.external_source_id,
        source_url=row.source_url,
        collected_at=row.collected_at,
        published_at=row.published_at,
        ingested_at=row.ingested_at,
        collector_version=row.collector_version,
        submitted_by=row.submitted_by,
    )


def _to_derivative(row: Row[tuple[Any, ...]]) -> AssetDerivative:
    return AssetDerivative(
        derivative_uuid=row.derivative_uuid,
        source_media_uuid=row.source_media_uuid,
        derived_media_uuid=row.derived_media_uuid,
        transform=row.transform,
        transform_version=row.transform_version,
        analysis_uuid=row.analysis_uuid,
        created_at=row.created_at,
    )


def _to_hold(row: Row[tuple[Any, ...]]) -> RetentionHold:
    return RetentionHold(
        hold_uuid=row.hold_uuid,
        media_uuid=row.media_uuid,
        reason=row.reason,
        placed_by=row.placed_by,
        placed_at=row.placed_at,
        released_at=row.released_at,
        released_by=row.released_by,
    )


class SqlAlchemyMediaRepository:
    """``MediaRepository`` over the ``media`` schema."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to an open session/transaction."""
        self._session = session

    async def add(self, asset: MediaAsset) -> MediaAsset:
        """Insert ``asset``."""
        try:
            await self._session.execute(
                media_assets.insert().values(
                    media_uuid=asset.media_uuid,
                    sha256=asset.sha256,
                    byte_size=asset.byte_size,
                    media_type=asset.media_type.value,
                    mime_type=asset.mime_type,
                    classification=asset.classification.value,
                    status=asset.status.value,
                    storage_domain=asset.storage_domain.value,
                    storage_bucket=asset.storage_bucket,
                    storage_key=asset.storage_key,
                    width=asset.width,
                    height=asset.height,
                    duration_ms=asset.duration_ms,
                    created_at=asset.created_at,
                    erased_at=asset.erased_at,
                )
            )
        except IntegrityError as exc:
            raise MediaError(f"media with hash {asset.sha256} is already held") from exc
        return asset

    async def get(self, media_uuid: UUID) -> MediaAsset | None:
        """Return the asset, or None."""
        result = await self._session.execute(
            select(media_assets).where(media_assets.c.media_uuid == media_uuid)
        )
        row = result.one_or_none()
        return None if row is None else _to_asset(row)

    async def find_by_hash(self, sha256: str) -> MediaAsset | None:
        """Return the asset with these exact bytes, or None."""
        result = await self._session.execute(
            select(media_assets).where(media_assets.c.sha256 == sha256)
        )
        row = result.one_or_none()
        return None if row is None else _to_asset(row)

    def _filtered(
        self, media_type: MediaType | None, classification: Classification | None
    ) -> list[Any]:
        conditions: list[Any] = []
        if media_type is not None:
            conditions.append(media_assets.c.media_type == media_type.value)
        if classification is not None:
            conditions.append(media_assets.c.classification == classification.value)
        return conditions

    async def list_assets(
        self,
        *,
        media_type: MediaType | None = None,
        classification: Classification | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[MediaAsset]:
        """Return assets newest first, filtered as asked."""
        statement = (
            select(media_assets)
            .where(*self._filtered(media_type, classification))
            .order_by(media_assets.c.created_at.desc(), media_assets.c.media_uuid.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(statement)
        return [_to_asset(row) for row in result.all()]

    async def count_assets(
        self,
        *,
        media_type: MediaType | None = None,
        classification: Classification | None = None,
    ) -> int:
        """Return how many assets match the filters."""
        result = await self._session.execute(
            select(func.count())
            .select_from(media_assets)
            .where(*self._filtered(media_type, classification))
        )
        return int(result.scalar_one())

    async def mark_erased(self, media_uuid: UUID, *, erased_at: datetime) -> bool:
        """Record that the bytes are gone, keeping the metadata for audit."""
        result = await self._session.execute(
            media_assets.update()
            .where(
                media_assets.c.media_uuid == media_uuid,
                media_assets.c.status != AssetStatus.ERASED.value,
            )
            .values(status=AssetStatus.ERASED.value, erased_at=erased_at)
        )
        # execute() is typed as Result; an UPDATE always yields a CursorResult,
        # which is what carries rowcount.
        return cast("CursorResult[Any]", result).rowcount > 0

    async def add_source(self, source: AssetSource) -> AssetSource:
        """Attach a provenance record, converging on repeated identical arrivals."""
        statement = (
            insert(media_asset_sources)
            .values(
                source_uuid=source.source_uuid,
                media_uuid=source.media_uuid,
                source_type=source.source_type.value,
                source_system=source.source_system,
                external_source_id=source.external_source_id,
                source_url=source.source_url,
                collected_at=source.collected_at,
                published_at=source.published_at,
                ingested_at=source.ingested_at,
                collector_version=source.collector_version,
                submitted_by=source.submitted_by,
            )
            .on_conflict_do_nothing(constraint="uq_media_asset_source")
        )
        try:
            await self._session.execute(statement)
        except IntegrityError as exc:
            raise MediaError(f"no media asset {source.media_uuid} to attach a source to") from exc
        return source

    async def list_sources(self, media_uuid: UUID) -> Sequence[AssetSource]:
        """Return every recorded arrival of this asset, oldest first."""
        result = await self._session.execute(
            select(media_asset_sources)
            .where(media_asset_sources.c.media_uuid == media_uuid)
            .order_by(media_asset_sources.c.ingested_at.asc())
        )
        return [_to_source(row) for row in result.all()]

    async def add_derivative(self, derivative: AssetDerivative) -> AssetDerivative:
        """Record that one asset was computed from another."""
        statement = (
            insert(media_asset_derivatives)
            .values(
                derivative_uuid=derivative.derivative_uuid,
                source_media_uuid=derivative.source_media_uuid,
                derived_media_uuid=derivative.derived_media_uuid,
                transform=derivative.transform,
                transform_version=derivative.transform_version,
                analysis_uuid=derivative.analysis_uuid,
                created_at=derivative.created_at,
            )
            .on_conflict_do_nothing(constraint="uq_media_asset_derivative")
        )
        try:
            await self._session.execute(statement)
        except IntegrityError as exc:
            raise MediaError("both sides of a derivative must be known media assets") from exc
        return derivative

    async def list_derivatives(self, media_uuid: UUID) -> Sequence[AssetDerivative]:
        """Return assets derived from this one."""
        result = await self._session.execute(
            select(media_asset_derivatives)
            .where(media_asset_derivatives.c.source_media_uuid == media_uuid)
            .order_by(media_asset_derivatives.c.created_at.asc())
        )
        return [_to_derivative(row) for row in result.all()]

    async def add_hold(self, hold: RetentionHold) -> RetentionHold:
        """Place a retention hold."""
        try:
            await self._session.execute(
                media_retention_holds.insert().values(
                    hold_uuid=hold.hold_uuid,
                    media_uuid=hold.media_uuid,
                    reason=hold.reason,
                    placed_by=hold.placed_by,
                    placed_at=hold.placed_at,
                    released_at=hold.released_at,
                    released_by=hold.released_by,
                )
            )
        except IntegrityError as exc:
            raise MediaError(f"no media asset {hold.media_uuid} to place a hold on") from exc
        return hold

    async def release_hold(
        self, hold_uuid: UUID, *, released_by: str, released_at: datetime
    ) -> bool:
        """Release a hold. Returns whether an active hold was released."""
        result = await self._session.execute(
            media_retention_holds.update()
            .where(
                media_retention_holds.c.hold_uuid == hold_uuid,
                media_retention_holds.c.released_at.is_(None),
            )
            .values(released_at=released_at, released_by=released_by)
        )
        return cast("CursorResult[Any]", result).rowcount > 0

    async def active_holds(self, media_uuid: UUID) -> Sequence[RetentionHold]:
        """Return the holds currently blocking erasure of this asset."""
        result = await self._session.execute(
            select(media_retention_holds)
            .where(
                media_retention_holds.c.media_uuid == media_uuid,
                media_retention_holds.c.released_at.is_(None),
            )
            .order_by(media_retention_holds.c.placed_at.asc())
        )
        return [_to_hold(row) for row in result.all()]
