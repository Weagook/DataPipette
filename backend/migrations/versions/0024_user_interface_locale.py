"""Persist the user interface locale.

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("locale", sa.String(length=5), server_default="en", nullable=False),
    )
    op.create_check_constraint("locale_valid", "users", "locale IN ('en', 'ru')")


def downgrade() -> None:
    op.drop_constraint("locale_valid", "users", type_="check")
    op.drop_column("users", "locale")
