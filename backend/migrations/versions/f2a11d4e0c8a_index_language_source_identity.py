"""Index imported language source identity.

Revision ID: f2a11d4e0c8a
Revises: d794d0c35b91
"""

from alembic import op
import sqlalchemy as sa

revision = "f2a11d4e0c8a"
down_revision = "d794d0c35b91"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("language_documents", sa.Column("source_id", sa.String(256)))
    op.add_column("language_documents", sa.Column("source_type", sa.String(64)))
    op.add_column("language_documents", sa.Column("profile_id", sa.String(256)))
    op.execute(
        """
        UPDATE language_documents
        SET source_id = COALESCE(attributes->>'source_id', attributes->>'platform_object_id', content_sha256),
            source_type = COALESCE(attributes->>'source_type', attributes->>'content_model', 'text'),
            profile_id = attributes->>'profile_id'
        """
    )
    op.alter_column("language_documents", "source_id", nullable=False)
    op.alter_column("language_documents", "source_type", nullable=False)
    op.create_index(
        "ix_language_documents_source_identity",
        "language_documents",
        ["source", "source_type", "source_id"],
    )
    op.create_index("ix_language_documents_profile_id", "language_documents", ["profile_id"])


def downgrade() -> None:
    op.drop_index("ix_language_documents_profile_id", table_name="language_documents")
    op.drop_index("ix_language_documents_source_identity", table_name="language_documents")
    op.drop_column("language_documents", "profile_id")
    op.drop_column("language_documents", "source_type")
    op.drop_column("language_documents", "source_id")
