"""identifications outlive the people they name

Revision ID: cde2c7d24322
Revises: 6904bc245c44
Create Date: 2026-08-19 13:36:03.313648

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "cde2c7d24322"
down_revision: Union[str, Sequence[str], None] = "6904bc245c44"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Drop the foreign key from identifications to persons.

    An identification is a historical record of a decision and must survive the
    deletion of the person it named, exactly as audit_events does. The
    constraint also made identification fail hard whenever the vector store
    still held a candidate the metadata store had already forgotten.
    """
    op.drop_constraint(
        "identifications_best_person_uuid_fkey", "identifications", type_="foreignkey"
    )


def downgrade() -> None:
    """Restore the foreign key. Fails if any identification names a deleted person."""
    op.create_foreign_key(
        "identifications_best_person_uuid_fkey",
        "identifications",
        "persons",
        ["best_person_uuid"],
        ["person_uuid"],
        ondelete="SET NULL",
    )
