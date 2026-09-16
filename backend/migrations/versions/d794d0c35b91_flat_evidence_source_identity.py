"""Index flat BlackGlass source identity.

Revision ID: d794d0c35b91
Revises: ab91e4c82730
"""

from alembic import op
import sqlalchemy as sa

revision = "d794d0c35b91"
down_revision = "ab91e4c82730"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("source_id", sa.String(256)), schema="evidence")
    op.add_column("runs", sa.Column("source_type", sa.String(64)), schema="evidence")
    op.execute(
        """
        UPDATE evidence.runs
        SET source_id = COALESCE(submission->>'source_id', submission#>>'{source,object_id}'),
            source_type = COALESCE(submission->>'source_type', submission#>>'{source,object_type}')
        """
    )
    op.alter_column("runs", "source_id", nullable=False, schema="evidence")
    op.alter_column("runs", "source_type", nullable=False, schema="evidence")
    op.create_index(
        "ix_evidence_runs_source",
        "runs",
        ["owner", "source_type", "source_id"],
        schema="evidence",
    )


def downgrade() -> None:
    op.drop_index("ix_evidence_runs_source", table_name="runs", schema="evidence")
    op.drop_column("runs", "source_type", schema="evidence")
    op.drop_column("runs", "source_id", schema="evidence")
