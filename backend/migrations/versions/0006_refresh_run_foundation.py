"""Add durable dataset refresh runs and idempotency mappings.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dataset_refresh_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_version_id", sa.Uuid(), nullable=False),
        sa.Column("requested_revision", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("dispatch_attempts", sa.Integer(), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_dataset_refresh_runs_attempt_count_nonnegative",
        ),
        sa.CheckConstraint(
            "dispatch_attempts >= 0",
            name="ck_dataset_refresh_runs_dispatch_attempts_nonnegative",
        ),
        sa.CheckConstraint(
            "requested_revision >= 1",
            name="ck_dataset_refresh_runs_requested_revision_positive",
        ),
        sa.CheckConstraint(
            "state IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_dataset_refresh_runs_state_valid",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["datasets.id"],
            name="fk_dataset_refresh_runs_dataset_id_datasets",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_version_id"],
            ["dataset_versions.id"],
            name="fk_dataset_refresh_runs_dataset_version_id_dataset_versions",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["users.id"],
            name="fk_dataset_refresh_runs_requested_by_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_dataset_refresh_runs"),
    )
    op.create_index(
        "ix_dataset_refresh_runs_dataset_id",
        "dataset_refresh_runs",
        ["dataset_id"],
    )
    op.create_index(
        "uq_dataset_refresh_runs_one_queued",
        "dataset_refresh_runs",
        ["dataset_id"],
        unique=True,
        postgresql_where=sa.text("state = 'queued'"),
    )
    op.create_index(
        "uq_dataset_refresh_runs_one_running",
        "dataset_refresh_runs",
        ["dataset_id"],
        unique=True,
        postgresql_where=sa.text("state = 'running'"),
    )
    op.create_table(
        "dataset_refresh_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("refresh_run_id", sa.Uuid(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name="fk_dataset_refresh_requests_actor_id_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["datasets.id"],
            name="fk_dataset_refresh_requests_dataset_id_datasets",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["refresh_run_id"],
            ["dataset_refresh_runs.id"],
            name="fk_dataset_refresh_requests_refresh_run_id_dataset_refresh_runs",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_dataset_refresh_requests"),
    )
    op.create_index(
        "ix_dataset_refresh_requests_dataset_id",
        "dataset_refresh_requests",
        ["dataset_id"],
    )
    op.create_index(
        "ix_dataset_refresh_requests_refresh_run_id",
        "dataset_refresh_requests",
        ["refresh_run_id"],
    )
    op.create_index(
        "uq_dataset_refresh_requests_actor_dataset_key",
        "dataset_refresh_requests",
        ["actor_id", "dataset_id", "idempotency_key_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_dataset_refresh_requests_actor_dataset_key",
        table_name="dataset_refresh_requests",
    )
    op.drop_index(
        "ix_dataset_refresh_requests_refresh_run_id",
        table_name="dataset_refresh_requests",
    )
    op.drop_index(
        "ix_dataset_refresh_requests_dataset_id",
        table_name="dataset_refresh_requests",
    )
    op.drop_table("dataset_refresh_requests")
    op.drop_index("uq_dataset_refresh_runs_one_running", table_name="dataset_refresh_runs")
    op.drop_index("uq_dataset_refresh_runs_one_queued", table_name="dataset_refresh_runs")
    op.drop_index("ix_dataset_refresh_runs_dataset_id", table_name="dataset_refresh_runs")
    op.drop_table("dataset_refresh_runs")
