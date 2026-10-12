"""Add private chart specifications and field dependencies.

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "charts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("definition", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("jsonb_typeof(definition) = 'object'", name="definition_object"),
        sa.CheckConstraint("revision >= 1", name="revision_positive"),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_charts_dataset_id", "charts", ["dataset_id"], unique=False)
    op.create_index("ix_charts_owner_id", "charts", ["owner_id"], unique=False)
    op.create_index(
        "ix_charts_dataset_created", "charts", ["dataset_id", "created_at"], unique=False
    )
    op.create_index(
        "uq_charts_owner_name_normalized",
        "charts",
        ["owner_id", sa.text("lower(name)")],
        unique=True,
    )
    op.create_table(
        "chart_field_dependencies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("chart_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("field_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["chart_id"], ["charts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_chart_field_dependencies_chart_id",
        "chart_field_dependencies",
        ["chart_id"],
        unique=False,
    )
    op.create_index(
        "ix_chart_field_dependencies_dataset_id",
        "chart_field_dependencies",
        ["dataset_id"],
        unique=False,
    )
    op.create_index(
        "ix_chart_field_dependencies_dataset_field",
        "chart_field_dependencies",
        ["dataset_id", "field_id"],
        unique=False,
    )
    op.create_index(
        "uq_chart_field_dependencies_chart_field",
        "chart_field_dependencies",
        ["chart_id", "field_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_chart_field_dependencies_chart_field",
        table_name="chart_field_dependencies",
    )
    op.drop_index(
        "ix_chart_field_dependencies_dataset_field",
        table_name="chart_field_dependencies",
    )
    op.drop_index(
        "ix_chart_field_dependencies_dataset_id",
        table_name="chart_field_dependencies",
    )
    op.drop_index(
        "ix_chart_field_dependencies_chart_id",
        table_name="chart_field_dependencies",
    )
    op.drop_table("chart_field_dependencies")
    op.drop_index("uq_charts_owner_name_normalized", table_name="charts")
    op.drop_index("ix_charts_dataset_created", table_name="charts")
    op.drop_index("ix_charts_owner_id", table_name="charts")
    op.drop_index("ix_charts_dataset_id", table_name="charts")
    op.drop_table("charts")
