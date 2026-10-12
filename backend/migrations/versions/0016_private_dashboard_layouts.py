"""Add private dashboard layouts and chart dependencies.

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dashboards",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("definition", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("jsonb_typeof(definition) = 'object'", name="definition_object"),
        sa.CheckConstraint("revision >= 1", name="revision_positive"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dashboards_owner_id", "dashboards", ["owner_id"], unique=False)
    op.create_index(
        "uq_dashboards_owner_name_normalized",
        "dashboards",
        ["owner_id", sa.text("lower(name)")],
        unique=True,
    )
    op.create_table(
        "dashboard_chart_dependencies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("dashboard_id", sa.Uuid(), nullable=False),
        sa.Column("chart_id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["chart_id"], ["charts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["dashboard_id"], ["dashboards.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_dashboard_chart_dependencies_chart_id",
        "dashboard_chart_dependencies",
        ["chart_id"],
        unique=False,
    )
    op.create_index(
        "ix_dashboard_chart_dependencies_dashboard_id",
        "dashboard_chart_dependencies",
        ["dashboard_id"],
        unique=False,
    )
    op.create_index(
        "uq_dashboard_chart_dependencies_dashboard_chart",
        "dashboard_chart_dependencies",
        ["dashboard_id", "chart_id"],
        unique=True,
    )
    op.create_index(
        "uq_dashboard_chart_dependencies_dashboard_item",
        "dashboard_chart_dependencies",
        ["dashboard_id", "item_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_dashboard_chart_dependencies_dashboard_item",
        table_name="dashboard_chart_dependencies",
    )
    op.drop_index(
        "uq_dashboard_chart_dependencies_dashboard_chart",
        table_name="dashboard_chart_dependencies",
    )
    op.drop_index(
        "ix_dashboard_chart_dependencies_dashboard_id",
        table_name="dashboard_chart_dependencies",
    )
    op.drop_index(
        "ix_dashboard_chart_dependencies_chart_id",
        table_name="dashboard_chart_dependencies",
    )
    op.drop_table("dashboard_chart_dependencies")
    op.drop_index("uq_dashboards_owner_name_normalized", table_name="dashboards")
    op.drop_index("ix_dashboards_owner_id", table_name="dashboards")
    op.drop_table("dashboards")
