"""Add source runtime budgets to Query Safety policy.

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns = (
        sa.Column(
            "preview_statement_timeout_seconds",
            sa.Integer(),
            nullable=False,
            server_default="5",
        ),
        sa.Column("preview_max_rows", sa.Integer(), nullable=False, server_default="200"),
        sa.Column(
            "preview_max_response_bytes",
            sa.BigInteger(),
            nullable=False,
            server_default="1048576",
        ),
        sa.Column(
            "refresh_statement_timeout_seconds",
            sa.Integer(),
            nullable=False,
            server_default="1800",
        ),
        sa.Column(
            "refresh_max_rows",
            sa.BigInteger(),
            nullable=False,
            server_default="10000000",
        ),
    )
    for column in columns:
        op.add_column("query_safety_policy", column)
    for column in columns:
        op.alter_column("query_safety_policy", column.name, server_default=None)


def downgrade() -> None:
    op.drop_column("query_safety_policy", "refresh_max_rows")
    op.drop_column("query_safety_policy", "refresh_statement_timeout_seconds")
    op.drop_column("query_safety_policy", "preview_max_response_bytes")
    op.drop_column("query_safety_policy", "preview_max_rows")
    op.drop_column("query_safety_policy", "preview_statement_timeout_seconds")
