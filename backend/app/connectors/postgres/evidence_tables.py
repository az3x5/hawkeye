"""Persistence for correlated evidence runs, source excerpts and delivery events."""

from sqlalchemy import (
    DDL,
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
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.connectors.postgres.media_tables import media_assets
from app.connectors.postgres.tables import metadata

event.listen(
    metadata, "before_create",
    DDL("CREATE SCHEMA IF NOT EXISTS evidence"),  # type: ignore[no-untyped-call]
)

runs = Table(
    "runs",
    metadata,
    Column("analysis_id", UUID(as_uuid=True), primary_key=True),
    Column("owner", String(256), nullable=False),
    Column("idempotency_key", String(64), nullable=False, unique=True),
    Column("source_id", String(256), nullable=False),
    Column("source_type", String(64), nullable=False),
    Column("submission", JSONB, nullable=False),
    Column("media_uuid", UUID(as_uuid=True), ForeignKey(media_assets.c.media_uuid)),
    Column("original_text", Text),
    Column("source_object", JSONB),
    Column("sha256", String(64), nullable=False),
    Column("status", String(32), nullable=False, server_default="queued"),
    Column("revision", Integer, nullable=False, server_default="0"),
    Column("result", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Index("ix_evidence_runs_owner", "owner", "created_at"),
    Index("ix_evidence_runs_source", "owner", "source_type", "source_id"),
    schema="evidence",
)

pieces = Table(
    "pieces",
    metadata,
    Column("evidence_id", UUID(as_uuid=True), primary_key=True),
    Column(
        "analysis_id", UUID(as_uuid=True), ForeignKey("evidence.runs.analysis_id"), nullable=False
    ),
    Column("body", JSONB, nullable=False),
    Column("search_text", Text, nullable=False),
    Index("ix_evidence_pieces_run", "analysis_id"),
    schema="evidence",
)

events = Table(
    "events",
    metadata,
    Column("sequence", Integer, primary_key=True, autoincrement=True),
    Column("event_id", UUID(as_uuid=True), nullable=False, unique=True),
    Column(
        "analysis_id", UUID(as_uuid=True), ForeignKey("evidence.runs.analysis_id"), nullable=False
    ),
    Column("owner", String(256), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("delivered_at", DateTime(timezone=True)),
    UniqueConstraint("analysis_id", "revision", name="uq_evidence_event_revision"),
    Index("ix_evidence_events_owner_cursor", "owner", "sequence"),
    schema="evidence",
)
