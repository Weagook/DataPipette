"""Add refresh estimate-versus-actual execution metrics.

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns = (
        ("planner_estimated_rows", "planner_estimated_rows_nonnegative"),
        ("actual_rows", "actual_rows_nonnegative"),
        ("execution_duration_ms", "execution_duration_ms_nonnegative"),
    )
    for column_name, _ in columns:
        op.add_column(
            "dataset_refresh_runs",
            sa.Column(column_name, sa.BigInteger(), nullable=True),
        )
    for column_name, constraint_name in columns:
        op.create_check_constraint(
            constraint_name,
            "dataset_refresh_runs",
            f"{column_name} IS NULL OR {column_name} >= 0",
        )


def downgrade() -> None:
    columns = (
        ("execution_duration_ms", "execution_duration_ms_nonnegative"),
        ("actual_rows", "actual_rows_nonnegative"),
        ("planner_estimated_rows", "planner_estimated_rows_nonnegative"),
    )
    for column_name, constraint_name in columns:
        op.drop_constraint(constraint_name, "dataset_refresh_runs", type_="check")
        op.drop_column("dataset_refresh_runs", column_name)
