"""Add identity groups and additive dataset ACL grants.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "identity_groups",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_identity_groups_name_normalized",
        "identity_groups",
        [sa.text("lower(name)")],
        unique=True,
    )
    op.create_table(
        "group_memberships",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("group_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["group_id"], ["identity_groups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_group_memberships_group_id", "group_memberships", ["group_id"])
    op.create_index("ix_group_memberships_user_id", "group_memberships", ["user_id"])
    op.create_index(
        "uq_group_memberships_group_user",
        "group_memberships",
        ["group_id", "user_id"],
        unique=True,
    )
    op.create_table(
        "dataset_acl_grants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("principal_type", sa.String(length=20), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("group_id", sa.Uuid(), nullable=True),
        sa.Column("permission", sa.String(length=30), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "principal_type IN ('user', 'group')",
            name="ck_dataset_acl_grants_principal_type_valid",
        ),
        sa.CheckConstraint(
            "(principal_type = 'user' AND user_id IS NOT NULL AND group_id IS NULL) OR "
            "(principal_type = 'group' AND group_id IS NOT NULL AND user_id IS NULL)",
            name="ck_dataset_acl_grants_principal_shape_valid",
        ),
        sa.CheckConstraint(
            "permission IN ('view_rows', 'export', 'manage')",
            name="ck_dataset_acl_grants_permission_valid",
        ),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["group_id"], ["identity_groups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dataset_acl_grants_dataset_id", "dataset_acl_grants", ["dataset_id"])
    op.create_index(
        "uq_dataset_acl_grants_user_permission",
        "dataset_acl_grants",
        ["dataset_id", "user_id", "permission"],
        unique=True,
        postgresql_where=sa.text("principal_type = 'user'"),
    )
    op.create_index(
        "uq_dataset_acl_grants_group_permission",
        "dataset_acl_grants",
        ["dataset_id", "group_id", "permission"],
        unique=True,
        postgresql_where=sa.text("principal_type = 'group'"),
    )


def downgrade() -> None:
    op.drop_table("dataset_acl_grants")
    op.drop_table("group_memberships")
    op.drop_table("identity_groups")
