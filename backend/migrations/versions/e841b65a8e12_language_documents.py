"""language documents

Revision ID: e841b65a8e12
Revises: b09c07269569
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e841b65a8e12"
down_revision: str | Sequence[str] | None = "b09c07269569"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "language_documents",
        sa.Column("document_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("source", sa.String(length=128), nullable=False),
        sa.Column("original_text", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("primary_script", sa.String(length=16), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "processing_state",
            sa.String(length=32),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("embedding_model", sa.String(length=256), nullable=True),
        sa.Column("embedding_version", sa.String(length=128), nullable=True),
        sa.Column("vector_collection", sa.String(length=255), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'", name="ck_language_document_sha256"),
        sa.CheckConstraint(
            "primary_script IN ('thaana', 'latin', 'mixed', 'none')",
            name="ck_language_document_script",
        ),
        sa.CheckConstraint(
            "processing_state IN ('pending', 'processed', 'failed')",
            name="ck_language_document_processing_state",
        ),
        sa.PrimaryKeyConstraint("document_uuid"),
        sa.UniqueConstraint("source", "content_sha256", name="uq_language_document_source_content"),
    )
    op.create_index("ix_language_documents_source", "language_documents", ["source"])
    op.create_index("ix_language_documents_state", "language_documents", ["processing_state"])


def downgrade() -> None:
    op.drop_index("ix_language_documents_state", table_name="language_documents")
    op.drop_index("ix_language_documents_source", table_name="language_documents")
    op.drop_table("language_documents")
