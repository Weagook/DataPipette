"""Add refresh cancellation and retry scheduling.

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_dataset_refresh_runs_state_valid",
        "dataset_refresh_runs",
        type_="check",
    )
    op.add_column(
        "dataset_refresh_runs",
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "dataset_refresh_runs",
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "dataset_refresh_runs",
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "dataset_refresh_runs",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_dataset_refresh_runs_state_valid",
        "dataset_refresh_runs",
        "state IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
    )
    op.create_check_constraint(
        "ck_dataset_refresh_runs_cancelled_timestamp_matches_state",
        "dataset_refresh_runs",
        "(state = 'cancelled') = (cancelled_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_dataset_refresh_runs_cancel_request_state_valid",
        "dataset_refresh_runs",
        "cancel_requested_at IS NULL OR state IN ('running', 'cancelled')",
    )
    op.create_check_constraint(
        "ck_dataset_refresh_runs_retry_due_only_when_queued",
        "dataset_refresh_runs",
        "next_attempt_at IS NULL OR state = 'queued'",
    )
    op.create_check_constraint(
        "ck_dataset_refresh_runs_lease_only_when_running",
        "dataset_refresh_runs",
        "lease_expires_at IS NULL OR state = 'running'",
    )
    op.create_index(
        "ix_dataset_refresh_runs_retry_dispatch",
        "dataset_refresh_runs",
        ["next_attempt_at", "requested_at"],
        unique=False,
        postgresql_where=sa.text("state = 'queued' AND dispatched_at IS NULL"),
    )
    op.create_index(
        "ix_dataset_refresh_runs_running_lease",
        "dataset_refresh_runs",
        ["lease_expires_at"],
        unique=False,
        postgresql_where=sa.text("state = 'running'"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_dataset_refresh_runs_running_lease",
        table_name="dataset_refresh_runs",
    )
    op.drop_index(
        "ix_dataset_refresh_runs_retry_dispatch",
        table_name="dataset_refresh_runs",
    )
    op.drop_constraint(
        "ck_dataset_refresh_runs_retry_due_only_when_queued",
        "dataset_refresh_runs",
        type_="check",
    )
    op.drop_constraint(
        "ck_dataset_refresh_runs_lease_only_when_running",
        "dataset_refresh_runs",
        type_="check",
    )
    op.drop_constraint(
        "ck_dataset_refresh_runs_cancel_request_state_valid",
        "dataset_refresh_runs",
        type_="check",
    )
    op.drop_constraint(
        "ck_dataset_refresh_runs_cancelled_timestamp_matches_state",
        "dataset_refresh_runs",
        type_="check",
    )
    op.drop_constraint(
        "ck_dataset_refresh_runs_state_valid",
        "dataset_refresh_runs",
        type_="check",
    )
    op.execute(
        "UPDATE dataset_refresh_runs SET state = 'failed', error_code = 'cancelled' "
        "WHERE state = 'cancelled'"
    )
    op.drop_column("dataset_refresh_runs", "cancelled_at")
    op.drop_column("dataset_refresh_runs", "cancel_requested_at")
    op.drop_column("dataset_refresh_runs", "next_attempt_at")
    op.drop_column("dataset_refresh_runs", "lease_expires_at")
    op.create_check_constraint(
        "ck_dataset_refresh_runs_state_valid",
        "dataset_refresh_runs",
        "state IN ('queued', 'running', 'succeeded', 'failed')",
    )
