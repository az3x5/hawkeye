"""Source-linked evidence runs and delivery events.

Revision ID: ab91e4c82730
Revises: f4a1c9e72b18
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "ab91e4c82730"
down_revision = "f4a1c9e72b18"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE SCHEMA IF NOT EXISTS evidence")
    op.create_table(
        "runs",
        sa.Column("analysis_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner", sa.String(256), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False, unique=True),
        sa.Column("submission", pg.JSONB, nullable=False),
        sa.Column("media_uuid", pg.UUID(as_uuid=True), sa.ForeignKey("media.assets.media_uuid")),
        sa.Column("original_text", sa.Text),
        sa.Column("source_object", pg.JSONB),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("revision", sa.Integer, nullable=False, server_default="0"),
        sa.Column("result", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema="evidence",
    )
    op.create_index("ix_evidence_runs_owner", "runs", ["owner", "created_at"], schema="evidence")
    op.create_table(
        "pieces",
        sa.Column("evidence_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "analysis_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("evidence.runs.analysis_id"),
            nullable=False,
        ),
        sa.Column("body", pg.JSONB, nullable=False),
        sa.Column("search_text", sa.Text, nullable=False),
        schema="evidence",
    )
    op.create_index("ix_evidence_pieces_run", "pieces", ["analysis_id"], schema="evidence")
    op.create_table(
        "events",
        sa.Column("sequence", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("event_id", pg.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column(
            "analysis_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("evidence.runs.analysis_id"),
            nullable=False,
        ),
        sa.Column("owner", sa.String(256), nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("payload", pg.JSONB, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("analysis_id", "revision", name="uq_evidence_event_revision"),
        schema="evidence",
    )
    op.create_index(
        "ix_evidence_events_owner_cursor", "events", ["owner", "sequence"], schema="evidence"
    )


def downgrade():
    op.drop_table("events", schema="evidence")
    op.drop_table("pieces", schema="evidence")
    op.drop_table("runs", schema="evidence")
    op.execute("DROP SCHEMA evidence")
