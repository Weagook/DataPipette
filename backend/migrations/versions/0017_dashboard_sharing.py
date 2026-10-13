"""Add additive dashboard view grants.

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dashboard_acl_grants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("dashboard_id", sa.Uuid(), nullable=False),
        sa.Column("principal_type", sa.String(length=20), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("group_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("principal_type IN ('user', 'group')", name="principal_type_valid"),
        sa.CheckConstraint(
            "(principal_type = 'user' AND user_id IS NOT NULL AND group_id IS NULL) OR "
            "(principal_type = 'group' AND group_id IS NOT NULL AND user_id IS NULL)",
            name="principal_shape_valid",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["dashboard_id"], ["dashboards.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["group_id"], ["identity_groups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_dashboard_acl_grants_dashboard_id",
        "dashboard_acl_grants",
        ["dashboard_id"],
        unique=False,
    )
    op.create_index(
        "uq_dashboard_acl_grants_user",
        "dashboard_acl_grants",
        ["dashboard_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("principal_type = 'user'"),
    )
    op.create_index(
        "uq_dashboard_acl_grants_group",
        "dashboard_acl_grants",
        ["dashboard_id", "group_id"],
        unique=True,
        postgresql_where=sa.text("principal_type = 'group'"),
    )


def downgrade() -> None:
    op.drop_index("uq_dashboard_acl_grants_group", table_name="dashboard_acl_grants")
    op.drop_index("uq_dashboard_acl_grants_user", table_name="dashboard_acl_grants")
    op.drop_index("ix_dashboard_acl_grants_dashboard_id", table_name="dashboard_acl_grants")
    op.drop_table("dashboard_acl_grants")
