"""PostgreSQL tables for durable asynchronous processing."""

from __future__ import annotations

from sqlalchemy import (
    DDL,
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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID

from app.connectors.postgres.tables import metadata

PROCESSING_SCHEMA = "processing"

# Keep metadata.create_all() useful for disposable PostgreSQL integration
# databases. Production creates the schema through the Alembic migration.
event.listen(
    metadata,
    "before_create",
    DDL(f"CREATE SCHEMA IF NOT EXISTS {PROCESSING_SCHEMA}"),
)

processing_jobs = Table(
    "jobs",
    metadata,
    Column("job_uuid", PgUUID(as_uuid=True), primary_key=True),
    Column("pipeline", String(64), nullable=False),
    Column("pipeline_version", String(128), nullable=False),
    Column("subject_type", String(64), nullable=False),
    Column("subject_uuid", PgUUID(as_uuid=True), nullable=False),
    Column("idempotency_key", String(256), nullable=False),
    Column("payload", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("status", String(32), nullable=False, server_default=text("'queued'")),
    Column("priority", Integer, nullable=False, server_default=text("20")),
    Column("attempt_count", Integer, nullable=False, server_default=text("0")),
    Column("max_attempts", Integer, nullable=False, server_default=text("3")),
    Column("queued_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("available_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("started_at", DateTime(timezone=True), nullable=True),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    Column("leased_until", DateTime(timezone=True), nullable=True),
    Column("lease_uuid", PgUUID(as_uuid=True), nullable=True),
    Column("worker_id", String(256), nullable=True),
    Column("error_code", String(64), nullable=True),
    Column("error_detail", Text(), nullable=True),
    UniqueConstraint("pipeline", "idempotency_key", name="uq_processing_job_idempotency"),
    CheckConstraint(
        "status IN ('queued', 'running', 'completed', 'failed', 'retry', "
        "'dead_letter', 'cancelled')",
        name="ck_processing_job_status",
    ),
    CheckConstraint("priority IN (0, 10, 20, 30, 40)", name="ck_processing_job_priority"),
    CheckConstraint("attempt_count >= 0", name="ck_processing_job_attempt_count"),
    CheckConstraint("max_attempts >= 1", name="ck_processing_job_max_attempts"),
    Index(
        "ix_processing_jobs_claim",
        "pipeline",
        "status",
        "available_at",
        "priority",
        "queued_at",
    ),
    Index("ix_processing_jobs_lease", "status", "leased_until"),
    Index("ix_processing_jobs_subject", "subject_type", "subject_uuid"),
    schema=PROCESSING_SCHEMA,
    comment="Durable work. PostgreSQL, not a transport queue, is authoritative.",
)

processing_job_attempts = Table(
    "job_attempts",
    metadata,
    Column("attempt_uuid", PgUUID(as_uuid=True), primary_key=True),
    Column(
        "job_uuid",
        PgUUID(as_uuid=True),
        ForeignKey("processing.jobs.job_uuid", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("attempt_number", Integer, nullable=False),
    Column("lease_uuid", PgUUID(as_uuid=True), nullable=False, unique=True),
    Column("worker_id", String(256), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    Column("outcome", String(32), nullable=True),
    Column("error_code", String(64), nullable=True),
    Column("error_detail", Text(), nullable=True),
    UniqueConstraint("job_uuid", "attempt_number", name="uq_processing_job_attempt_number"),
    CheckConstraint("attempt_number >= 1", name="ck_processing_job_attempt_number"),
    CheckConstraint(
        "outcome IS NULL OR outcome IN "
        "('completed', 'failed', 'retry', 'dead_letter', 'cancelled', 'lease_expired')",
        name="ck_processing_job_attempt_outcome",
    ),
    Index("ix_processing_job_attempts_job", "job_uuid", "attempt_number"),
    schema=PROCESSING_SCHEMA,
    comment="History of every worker attempt and lease outcome.",
)


processing_outbox_events = Table(
    "outbox_events",
    metadata,
    Column("event_uuid", PgUUID(as_uuid=True), primary_key=True),
    Column("aggregate_type", String(64), nullable=False),
    Column("aggregate_uuid", PgUUID(as_uuid=True), nullable=False),
    Column("event_type", String(128), nullable=False),
    Column("payload", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("available_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("published_at", DateTime(timezone=True), nullable=True),
    Column("attempt_count", Integer, nullable=False, server_default=text("0")),
    Column("lease_uuid", PgUUID(as_uuid=True), nullable=True),
    Column("leased_until", DateTime(timezone=True), nullable=True),
    Column("last_error", Text(), nullable=True),
    CheckConstraint("attempt_count >= 0", name="ck_processing_outbox_attempt_count"),
    Index(
        "ix_processing_outbox_delivery",
        "published_at",
        "available_at",
        "leased_until",
        "created_at",
    ),
    schema=PROCESSING_SCHEMA,
    comment="Transactional events awaiting optional notification or integration delivery.",
)
processing_worker_heartbeats = Table(
    "worker_heartbeats",
    metadata,
    Column("worker_id", String(256), primary_key=True),
    Column("pipeline", String(64), primary_key=True),
    Column("last_seen_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("current_job_uuid", PgUUID(as_uuid=True), nullable=True),
    Column("details", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Index("ix_processing_worker_heartbeats_seen", "last_seen_at"),
    schema=PROCESSING_SCHEMA,
    comment="Operational worker presence; not the authority for job ownership.",
)

__all__ = [
    "PROCESSING_SCHEMA",
    "processing_job_attempts",
    "processing_jobs",
    "processing_worker_heartbeats",
    "processing_outbox_events",
]
