"""Add governed background runtime budgets and export provenance.

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns = (
        sa.Column(
            "export_timeout_seconds",
            sa.Integer(),
            nullable=False,
            server_default="1800",
        ),
        sa.Column(
            "export_memory_limit_mb",
            sa.Integer(),
            nullable=False,
            server_default="512",
        ),
        sa.Column(
            "export_threads",
            sa.Integer(),
            nullable=False,
            server_default="2",
        ),
        sa.Column(
            "export_temp_limit_mb",
            sa.Integer(),
            nullable=False,
            server_default="2048",
        ),
        sa.Column(
            "export_max_rows",
            sa.Integer(),
            nullable=False,
            server_default="5000000",
        ),
        sa.Column(
            "export_max_bytes",
            sa.BigInteger(),
            nullable=False,
            server_default="1073741824",
        ),
        sa.Column(
            "background_job_concurrency",
            sa.Integer(),
            nullable=False,
            server_default="2",
        ),
    )
    for column in columns:
        op.add_column("query_safety_policy", column)
    for column in columns:
        op.alter_column("query_safety_policy", column.name, server_default=None)

    op.add_column(
        "dataset_refresh_runs",
        sa.Column("policy_revision", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "policy_revision_nonnegative",
        "dataset_refresh_runs",
        "policy_revision IS NULL OR policy_revision >= 0",
    )
    op.add_column(
        "dataset_exports",
        sa.Column("policy_revision", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "policy_revision_nonnegative",
        "dataset_exports",
        "policy_revision IS NULL OR policy_revision >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "policy_revision_nonnegative",
        "dataset_exports",
        type_="check",
    )
    op.drop_column("dataset_exports", "policy_revision")
    op.drop_constraint(
        "policy_revision_nonnegative",
        "dataset_refresh_runs",
        type_="check",
    )
    op.drop_column("dataset_refresh_runs", "policy_revision")
    op.drop_column("query_safety_policy", "background_job_concurrency")
    op.drop_column("query_safety_policy", "export_max_bytes")
    op.drop_column("query_safety_policy", "export_max_rows")
    op.drop_column("query_safety_policy", "export_temp_limit_mb")
    op.drop_column("query_safety_policy", "export_threads")
    op.drop_column("query_safety_policy", "export_memory_limit_mb")
    op.drop_column("query_safety_policy", "export_timeout_seconds")
