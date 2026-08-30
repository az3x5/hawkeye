"""durable processing jobs

Revision ID: 79b8c31f4d2a
Revises: e841b65a8e12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "79b8c31f4d2a"
down_revision: str | Sequence[str] | None = "e841b65a8e12"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "processing"


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    op.create_table(
        "jobs",
        sa.Column("job_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pipeline", sa.String(length=64), nullable=False),
        sa.Column("pipeline_version", sa.String(length=128), nullable=False),
        sa.Column("subject_type", sa.String(length=64), nullable=False),
        sa.Column("subject_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=256), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=32), server_default="queued", nullable=False),
        sa.Column("priority", sa.Integer(), server_default="20", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column(
            "queued_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("leased_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_uuid", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("worker_id", sa.String(length=256), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.CheckConstraint("attempt_count >= 0", name="ck_processing_job_attempt_count"),
        sa.CheckConstraint("max_attempts >= 1", name="ck_processing_job_max_attempts"),
        sa.CheckConstraint("priority IN (0, 10, 20, 30, 40)", name="ck_processing_job_priority"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'retry', "
            "'dead_letter', 'cancelled')",
            name="ck_processing_job_status",
        ),
        sa.PrimaryKeyConstraint("job_uuid"),
        sa.UniqueConstraint("pipeline", "idempotency_key", name="uq_processing_job_idempotency"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_processing_jobs_claim",
        "jobs",
        ["pipeline", "status", "available_at", "priority", "queued_at"],
        schema=SCHEMA,
    )
    op.create_index("ix_processing_jobs_lease", "jobs", ["status", "leased_until"], schema=SCHEMA)
    op.create_index(
        "ix_processing_jobs_subject", "jobs", ["subject_type", "subject_uuid"], schema=SCHEMA
    )

    op.create_table(
        "job_attempts",
        sa.Column("attempt_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("lease_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("worker_id", sa.String(length=256), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.CheckConstraint("attempt_number >= 1", name="ck_processing_job_attempt_number"),
        sa.CheckConstraint(
            "outcome IS NULL OR outcome IN "
            "('completed', 'failed', 'retry', 'dead_letter', 'cancelled', 'lease_expired')",
            name="ck_processing_job_attempt_outcome",
        ),
        sa.ForeignKeyConstraint(["job_uuid"], ["processing.jobs.job_uuid"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("attempt_uuid"),
        sa.UniqueConstraint("job_uuid", "attempt_number", name="uq_processing_job_attempt_number"),
        sa.UniqueConstraint("lease_uuid"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_processing_job_attempts_job",
        "job_attempts",
        ["job_uuid", "attempt_number"],
        schema=SCHEMA,
    )

    op.create_table(
        "outbox_events",
        sa.Column("event_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("aggregate_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("lease_uuid", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("leased_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.CheckConstraint("attempt_count >= 0", name="ck_processing_outbox_attempt_count"),
        sa.PrimaryKeyConstraint("event_uuid"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_processing_outbox_delivery",
        "outbox_events",
        ["published_at", "available_at", "leased_until", "created_at"],
        schema=SCHEMA,
    )

    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_id", sa.String(length=256), nullable=False),
        sa.Column("pipeline", sa.String(length=64), nullable=False),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("current_job_uuid", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("worker_id", "pipeline"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_processing_worker_heartbeats_seen",
        "worker_heartbeats",
        ["last_seen_at"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_processing_worker_heartbeats_seen",
        table_name="worker_heartbeats",
        schema=SCHEMA,
    )
    op.drop_table("worker_heartbeats", schema=SCHEMA)
    op.drop_index(
        "ix_processing_outbox_delivery",
        table_name="outbox_events",
        schema=SCHEMA,
    )
    op.drop_table("outbox_events", schema=SCHEMA)
    op.drop_index("ix_processing_job_attempts_job", table_name="job_attempts", schema=SCHEMA)
    op.drop_table("job_attempts", schema=SCHEMA)
    op.drop_index("ix_processing_jobs_subject", table_name="jobs", schema=SCHEMA)
    op.drop_index("ix_processing_jobs_lease", table_name="jobs", schema=SCHEMA)
    op.drop_index("ix_processing_jobs_claim", table_name="jobs", schema=SCHEMA)
    op.drop_table("jobs", schema=SCHEMA)
    op.execute(f"DROP SCHEMA IF EXISTS {SCHEMA}")
