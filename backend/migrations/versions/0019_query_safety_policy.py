"""Add managed Query Safety policy.

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "query_safety_policy",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("estimated_rows_warning", sa.BigInteger(), nullable=False),
        sa.Column("estimated_rows_block", sa.BigInteger(), nullable=False),
        sa.Column("total_cost_warning", sa.Float(), nullable=False),
        sa.Column("total_cost_block", sa.Float(), nullable=False),
        sa.Column("join_count_warning", sa.Integer(), nullable=False),
        sa.Column("join_count_block", sa.Integer(), nullable=False),
        sa.Column("sequential_scan_rows_warning", sa.BigInteger(), nullable=False),
        sa.Column("sort_rows_warning", sa.BigInteger(), nullable=False),
        sa.Column("stale_statistics_days", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name="singleton"),
        sa.CheckConstraint("revision >= 1", name="revision_positive"),
        sa.CheckConstraint(
            "estimated_rows_warning < estimated_rows_block",
            name="estimated_rows_ordered",
        ),
        sa.CheckConstraint("total_cost_warning < total_cost_block", name="total_cost_ordered"),
        sa.CheckConstraint("join_count_warning < join_count_block", name="join_count_ordered"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("query_safety_policy")
