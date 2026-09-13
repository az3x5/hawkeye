"""identifications record what was attested about the capture

Revision ID: f4a1c9e72b18
Revises: c3f7a91d8b40
Create Date: 2026-09-13 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f4a1c9e72b18"
down_revision: str | Sequence[str] | None = "c3f7a91d8b40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Record the capture assurance that applied to each identification.

    The system has no presentation-attack detection, so an image whose origin
    is unattested can be a photograph of a photograph. That fact is part of the
    rules a decision was made under, and it is stored alongside the thresholds
    for the same reason: a past decision must stay readable against the policy
    that actually produced it.

    Existing rows are backfilled as 'unsupervised' rather than 'supervised'.
    Nobody attested to those captures — the concept did not exist when they
    were made — and recording an attestation that never happened would put a
    false claim in an audit record.
    """
    op.add_column(
        "identifications",
        sa.Column(
            "capture_assurance",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'unsupervised'"),
        ),
    )
    op.create_check_constraint(
        "ck_identification_capture_assurance",
        "identifications",
        "capture_assurance IN ('supervised', 'unsupervised')",
    )


def downgrade() -> None:
    """Drop the column, losing what was attested about past captures."""
    op.drop_constraint("ck_identification_capture_assurance", "identifications", type_="check")
    op.drop_column("identifications", "capture_assurance")
