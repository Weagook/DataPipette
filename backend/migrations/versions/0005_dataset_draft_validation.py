"""Add revision-bound dataset draft validation artifacts.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "dataset_versions",
        sa.Column(
            "validation",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "validation_object",
        "dataset_versions",
        "validation IS NULL OR jsonb_typeof(validation) = 'object'",
    )


def downgrade() -> None:
    op.drop_constraint(
        "validation_object",
        "dataset_versions",
        type_="check",
    )
    op.drop_column("dataset_versions", "validation")
