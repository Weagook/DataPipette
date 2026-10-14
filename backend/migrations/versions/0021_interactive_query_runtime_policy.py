"""Add interactive DuckDB runtime budgets to Query Safety policy.

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns = (
        sa.Column(
            "interactive_query_timeout_seconds",
            sa.Float(),
            nullable=False,
            server_default="10",
        ),
        sa.Column(
            "interactive_query_memory_limit_mb",
            sa.Integer(),
            nullable=False,
            server_default="256",
        ),
        sa.Column(
            "interactive_query_threads",
            sa.Integer(),
            nullable=False,
            server_default="2",
        ),
        sa.Column(
            "interactive_query_concurrency",
            sa.Integer(),
            nullable=False,
            server_default="4",
        ),
        sa.Column(
            "interactive_query_temp_limit_mb",
            sa.Integer(),
            nullable=False,
            server_default="512",
        ),
        sa.Column(
            "interactive_query_max_response_bytes",
            sa.BigInteger(),
            nullable=False,
            server_default="2097152",
        ),
    )
    for column in columns:
        op.add_column("query_safety_policy", column)
    for column in columns:
        op.alter_column("query_safety_policy", column.name, server_default=None)


def downgrade() -> None:
    op.drop_column("query_safety_policy", "interactive_query_max_response_bytes")
    op.drop_column("query_safety_policy", "interactive_query_temp_limit_mb")
    op.drop_column("query_safety_policy", "interactive_query_concurrency")
    op.drop_column("query_safety_policy", "interactive_query_threads")
    op.drop_column("query_safety_policy", "interactive_query_memory_limit_mb")
    op.drop_column("query_safety_policy", "interactive_query_timeout_seconds")
