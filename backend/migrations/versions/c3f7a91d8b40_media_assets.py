"""first-class media assets

Revision ID: c3f7a91d8b40
Revises: 79b8c31f4d2a
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c3f7a91d8b40"
down_revision: str | Sequence[str] | None = "79b8c31f4d2a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "media"


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    op.create_table(
        "assets",
        sa.Column("media_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("media_type", sa.String(length=16), nullable=False),
        sa.Column("mime_type", sa.String(length=128), nullable=False),
        sa.Column(
            "classification", sa.String(length=16), server_default="internal", nullable=False
        ),
        sa.Column("status", sa.String(length=16), server_default="stored", nullable=False),
        sa.Column("storage_domain", sa.String(length=32), nullable=False),
        sa.Column("storage_bucket", sa.String(length=63), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("erased_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("byte_size > 0", name="ck_media_asset_byte_size"),
        sa.CheckConstraint(
            "classification IN ('public', 'internal', 'restricted', 'biometric')",
            name="ck_media_asset_classification",
        ),
        sa.CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0", name="ck_media_asset_duration"
        ),
        sa.CheckConstraint(
            "(status = 'erased') = (erased_at IS NOT NULL)", name="ck_media_asset_erased_at"
        ),
        sa.CheckConstraint("height IS NULL OR height > 0", name="ck_media_asset_height"),
        sa.CheckConstraint(
            "media_type IN ('image', 'video', 'audio', 'document')",
            name="ck_media_asset_media_type",
        ),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_media_asset_sha256"),
        sa.CheckConstraint(
            "status IN ('stored', 'quarantined', 'erased')", name="ck_media_asset_status"
        ),
        sa.CheckConstraint(
            "storage_domain IN ('media', 'derived', 'biometric-reference', 'biometric-observed')",
            name="ck_media_asset_storage_domain",
        ),
        sa.CheckConstraint("width IS NULL OR width > 0", name="ck_media_asset_width"),
        sa.PrimaryKeyConstraint("media_uuid"),
        sa.UniqueConstraint("sha256"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_media_assets_created_at", "assets", [sa.text("created_at DESC")], schema=SCHEMA
    )
    op.create_index(
        "ix_media_assets_type_created",
        "assets",
        ["media_type", sa.text("created_at DESC")],
        schema=SCHEMA,
    )
    op.create_index("ix_media_assets_classification", "assets", ["classification"], schema=SCHEMA)

    op.create_table(
        "asset_sources",
        sa.Column("source_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("media_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_system", sa.String(length=128), nullable=False),
        sa.Column("external_source_id", sa.String(length=256), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("collector_version", sa.String(length=128), nullable=True),
        sa.Column("submitted_by", sa.String(length=256), nullable=True),
        sa.CheckConstraint(
            "source_type IN ('upload', 'identity_server', 'blackglass', 'news', 'social', "
            "'camera', 'document', 'api', 'other')",
            name="ck_media_asset_source_type",
        ),
        sa.ForeignKeyConstraint(
            ["media_uuid"], [f"{SCHEMA}.assets.media_uuid"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("source_uuid"),
        sa.UniqueConstraint(
            "media_uuid",
            "source_type",
            "source_system",
            "external_source_id",
            name="uq_media_asset_source",
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_media_asset_sources_media", "asset_sources", ["media_uuid"], schema=SCHEMA)
    op.create_index(
        "ix_media_asset_sources_external",
        "asset_sources",
        ["source_system", "external_source_id"],
        schema=SCHEMA,
    )

    op.create_table(
        "asset_derivatives",
        sa.Column("derivative_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_media_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("derived_media_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("transform", sa.String(length=64), nullable=False),
        sa.Column("transform_version", sa.String(length=128), nullable=False),
        sa.Column("analysis_uuid", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source_media_uuid <> derived_media_uuid", name="ck_media_derivative_not_self"
        ),
        sa.ForeignKeyConstraint(
            ["derived_media_uuid"], [f"{SCHEMA}.assets.media_uuid"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_media_uuid"], [f"{SCHEMA}.assets.media_uuid"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("derivative_uuid"),
        sa.UniqueConstraint(
            "source_media_uuid",
            "derived_media_uuid",
            "transform",
            "transform_version",
            name="uq_media_asset_derivative",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_media_derivatives_source", "asset_derivatives", ["source_media_uuid"], schema=SCHEMA
    )
    op.create_index(
        "ix_media_derivatives_derived", "asset_derivatives", ["derived_media_uuid"], schema=SCHEMA
    )

    op.create_table(
        "retention_holds",
        sa.Column("hold_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("media_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("placed_by", sa.String(length=256), nullable=False),
        sa.Column(
            "placed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_by", sa.String(length=256), nullable=True),
        sa.CheckConstraint(
            "(released_at IS NULL) = (released_by IS NULL)", name="ck_media_hold_release"
        ),
        sa.ForeignKeyConstraint(
            ["media_uuid"], [f"{SCHEMA}.assets.media_uuid"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("hold_uuid"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_media_holds_active",
        "retention_holds",
        ["media_uuid"],
        schema=SCHEMA,
        postgresql_where=sa.text("released_at IS NULL"),
    )


def downgrade() -> None:
    # Drops every media record and its provenance. Export before running this
    # in anger: the object bytes survive in the bucket, but the rows that say
    # what they are and where they came from do not.
    op.drop_table("retention_holds", schema=SCHEMA)
    op.drop_table("asset_derivatives", schema=SCHEMA)
    op.drop_table("asset_sources", schema=SCHEMA)
    op.drop_table("assets", schema=SCHEMA)
    op.execute(f"DROP SCHEMA IF EXISTS {SCHEMA}")
