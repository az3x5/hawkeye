"""PostgreSQL tables for first-class media assets.

In its own schema because media has a different privilege boundary from the
identity tables in ``public``: a future read-only analytics role should be
able to see that an asset exists without being able to read the person it was
enrolled for.
"""

from __future__ import annotations

from sqlalchemy import (
    DDL,
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID

from app.connectors.postgres.tables import metadata

MEDIA_SCHEMA = "media"

# Keeps metadata.create_all() usable for disposable integration databases.
# Production creates the schema through the Alembic migration.
event.listen(
    metadata,
    "before_create",
    DDL(f"CREATE SCHEMA IF NOT EXISTS {MEDIA_SCHEMA}"),  # type: ignore[no-untyped-call]
)

media_assets = Table(
    "assets",
    metadata,
    Column("media_uuid", PgUUID(as_uuid=True), primary_key=True),
    # Unique: identical bytes are one asset. Provenance lives in asset_sources.
    Column("sha256", String(64), nullable=False, unique=True),
    Column("byte_size", BigInteger, nullable=False),
    Column("media_type", String(16), nullable=False),
    Column("mime_type", String(128), nullable=False),
    Column("classification", String(16), nullable=False, server_default=text("'internal'")),
    Column("status", String(16), nullable=False, server_default=text("'stored'")),
    Column("storage_domain", String(32), nullable=False),
    Column("storage_bucket", String(63), nullable=False),
    Column("storage_key", String(512), nullable=False),
    Column("width", Integer, nullable=True),
    Column("height", Integer, nullable=True),
    Column("duration_ms", BigInteger, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("erased_at", DateTime(timezone=True), nullable=True),
    CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_media_asset_sha256"),
    CheckConstraint("byte_size > 0", name="ck_media_asset_byte_size"),
    CheckConstraint(
        "media_type IN ('image', 'video', 'audio', 'document')",
        name="ck_media_asset_media_type",
    ),
    CheckConstraint(
        "classification IN ('public', 'internal', 'restricted', 'biometric')",
        name="ck_media_asset_classification",
    ),
    CheckConstraint("status IN ('stored', 'quarantined', 'erased')", name="ck_media_asset_status"),
    CheckConstraint(
        "storage_domain IN ('media', 'derived', 'biometric-reference', 'biometric-observed')",
        name="ck_media_asset_storage_domain",
    ),
    CheckConstraint("width IS NULL OR width > 0", name="ck_media_asset_width"),
    CheckConstraint("height IS NULL OR height > 0", name="ck_media_asset_height"),
    CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="ck_media_asset_duration"),
    # An erased asset must say when, and a stored one must not claim to have
    # been erased. Without this the readable/erased split is only a convention.
    CheckConstraint(
        "(status = 'erased') = (erased_at IS NOT NULL)", name="ck_media_asset_erased_at"
    ),
    Index("ix_media_assets_created_at", text("created_at DESC")),
    Index("ix_media_assets_type_created", "media_type", text("created_at DESC")),
    Index("ix_media_assets_classification", "classification"),
    schema=MEDIA_SCHEMA,
    comment="Immutable media. One row per distinct byte sequence.",
)

media_asset_sources = Table(
    "asset_sources",
    metadata,
    Column("source_uuid", PgUUID(as_uuid=True), primary_key=True),
    Column(
        "media_uuid",
        PgUUID(as_uuid=True),
        ForeignKey(f"{MEDIA_SCHEMA}.assets.media_uuid", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("source_type", String(32), nullable=False),
    Column("source_system", String(128), nullable=False),
    Column("external_source_id", String(256), nullable=True),
    Column("source_url", Text, nullable=True),
    Column("collected_at", DateTime(timezone=True), nullable=True),
    Column("published_at", DateTime(timezone=True), nullable=True),
    Column("ingested_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("collector_version", String(128), nullable=True),
    Column("submitted_by", String(256), nullable=True),
    # Makes re-delivery of the same arrival converge instead of accumulating
    # duplicate provenance. A NULL external id is not deduplicated, which is
    # correct: two anonymous uploads of one image really are two arrivals.
    UniqueConstraint(
        "media_uuid",
        "source_type",
        "source_system",
        "external_source_id",
        name="uq_media_asset_source",
    ),
    CheckConstraint(
        "source_type IN ('upload', 'identity_server', 'blackglass', 'news', 'social', "
        "'camera', 'document', 'api', 'other')",
        name="ck_media_asset_source_type",
    ),
    Index("ix_media_asset_sources_media", "media_uuid"),
    Index("ix_media_asset_sources_external", "source_system", "external_source_id"),
    schema=MEDIA_SCHEMA,
    comment="Every arrival of an asset. Deduplication adds rows here, never removes them.",
)

media_asset_derivatives = Table(
    "asset_derivatives",
    metadata,
    Column("derivative_uuid", PgUUID(as_uuid=True), primary_key=True),
    Column(
        "source_media_uuid",
        PgUUID(as_uuid=True),
        ForeignKey(f"{MEDIA_SCHEMA}.assets.media_uuid", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "derived_media_uuid",
        PgUUID(as_uuid=True),
        ForeignKey(f"{MEDIA_SCHEMA}.assets.media_uuid", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("transform", String(64), nullable=False),
    Column("transform_version", String(128), nullable=False),
    Column("analysis_uuid", PgUUID(as_uuid=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    UniqueConstraint(
        "source_media_uuid",
        "derived_media_uuid",
        "transform",
        "transform_version",
        name="uq_media_asset_derivative",
    ),
    CheckConstraint("source_media_uuid <> derived_media_uuid", name="ck_media_derivative_not_self"),
    Index("ix_media_derivatives_source", "source_media_uuid"),
    Index("ix_media_derivatives_derived", "derived_media_uuid"),
    schema=MEDIA_SCHEMA,
    comment="Lineage. Reprocessing adds a row; it never rewrites one.",
)

media_retention_holds = Table(
    "retention_holds",
    metadata,
    Column("hold_uuid", PgUUID(as_uuid=True), primary_key=True),
    Column(
        "media_uuid",
        PgUUID(as_uuid=True),
        ForeignKey(f"{MEDIA_SCHEMA}.assets.media_uuid", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("reason", Text, nullable=False),
    Column("placed_by", String(256), nullable=False),
    Column("placed_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("released_at", DateTime(timezone=True), nullable=True),
    Column("released_by", String(256), nullable=True),
    CheckConstraint("(released_at IS NULL) = (released_by IS NULL)", name="ck_media_hold_release"),
    Index("ix_media_holds_active", "media_uuid", postgresql_where=text("released_at IS NULL")),
    schema=MEDIA_SCHEMA,
    comment="Standing instructions that an asset must not be erased.",
)
