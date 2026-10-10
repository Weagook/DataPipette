"""Add dataset refresh schedules and run trigger provenance.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dataset_refresh_schedules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("interval_minutes", sa.Integer(), nullable=True),
        sa.Column("daily_time", sa.Time(timezone=False), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('interval', 'daily')", name="kind_valid"),
        sa.CheckConstraint(
            "(kind = 'interval' AND interval_minutes BETWEEN 5 AND 10080 "
            "AND daily_time IS NULL) OR "
            "(kind = 'daily' AND interval_minutes IS NULL AND daily_time IS NOT NULL)",
            name="cadence_shape_valid",
        ),
        sa.CheckConstraint("revision >= 1", name="revision_positive"),
        sa.CheckConstraint(
            "(enabled AND next_run_at IS NOT NULL) OR (NOT enabled AND next_run_at IS NULL)",
            name="enabled_next_run_valid",
        ),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dataset_id"),
    )
    op.create_index(
        "ix_dataset_refresh_schedules_due",
        "dataset_refresh_schedules",
        ["next_run_at"],
        postgresql_where=sa.text("enabled"),
    )
    op.add_column(
        "dataset_refresh_runs",
        sa.Column("trigger_type", sa.String(length=20), nullable=False, server_default="manual"),
    )
    op.add_column(
        "dataset_refresh_runs",
        sa.Column("schedule_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_dataset_refresh_runs_schedule_id_dataset_refresh_schedules",
        "dataset_refresh_runs",
        "dataset_refresh_schedules",
        ["schedule_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_dataset_refresh_runs_schedule_id",
        "dataset_refresh_runs",
        ["schedule_id"],
    )
    op.create_check_constraint(
        "ck_dataset_refresh_runs_trigger_shape_valid",
        "dataset_refresh_runs",
        "(trigger_type = 'manual' AND schedule_id IS NULL) OR "
        "(trigger_type = 'scheduled' AND schedule_id IS NOT NULL)",
    )
    op.alter_column("dataset_refresh_runs", "trigger_type", server_default=None)


def downgrade() -> None:
    op.drop_constraint(
        "ck_dataset_refresh_runs_trigger_shape_valid",
        "dataset_refresh_runs",
        type_="check",
    )
    op.drop_index("ix_dataset_refresh_runs_schedule_id", table_name="dataset_refresh_runs")
    op.drop_constraint(
        "fk_dataset_refresh_runs_schedule_id_dataset_refresh_schedules",
        "dataset_refresh_runs",
        type_="foreignkey",
    )
    op.drop_column("dataset_refresh_runs", "schedule_id")
    op.drop_column("dataset_refresh_runs", "trigger_type")
    op.drop_table("dataset_refresh_schedules")
