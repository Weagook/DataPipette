"""Add scoped PostgreSQL connection grants.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "connection_access_grants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("principal_type", sa.String(length=20), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("group_id", sa.Uuid(), nullable=True),
        sa.Column("scope_type", sa.String(length=20), nullable=False),
        sa.Column("schema_name", sa.String(length=255), nullable=True),
        sa.Column("relation_name", sa.String(length=255), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "principal_type IN ('user', 'group')",
            name="ck_connection_access_grants_principal_type_valid",
        ),
        sa.CheckConstraint(
            "(principal_type = 'user' AND user_id IS NOT NULL AND group_id IS NULL) OR "
            "(principal_type = 'group' AND group_id IS NOT NULL AND user_id IS NULL)",
            name="ck_connection_access_grants_principal_shape_valid",
        ),
        sa.CheckConstraint(
            "scope_type IN ('all', 'schema', 'table')",
            name="ck_connection_access_grants_scope_type_valid",
        ),
        sa.CheckConstraint(
            "(scope_type = 'all' AND schema_name IS NULL AND relation_name IS NULL) OR "
            "(scope_type = 'schema' AND schema_name IS NOT NULL AND relation_name IS NULL) OR "
            "(scope_type = 'table' AND schema_name IS NOT NULL AND relation_name IS NOT NULL)",
            name="ck_connection_access_grants_scope_shape_valid",
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"], ["postgresql_connections.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["group_id"], ["identity_groups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_connection_access_grants_connection_id",
        "connection_access_grants",
        ["connection_id"],
    )
    for principal in ("user", "group"):
        principal_column = f"{principal}_id"
        op.create_index(
            f"uq_connection_access_{principal}_all",
            "connection_access_grants",
            ["connection_id", principal_column],
            unique=True,
            postgresql_where=sa.text(f"principal_type = '{principal}' AND scope_type = 'all'"),
        )
        op.create_index(
            f"uq_connection_access_{principal}_schema",
            "connection_access_grants",
            ["connection_id", principal_column, "schema_name"],
            unique=True,
            postgresql_where=sa.text(f"principal_type = '{principal}' AND scope_type = 'schema'"),
        )
        op.create_index(
            f"uq_connection_access_{principal}_table",
            "connection_access_grants",
            ["connection_id", principal_column, "schema_name", "relation_name"],
            unique=True,
            postgresql_where=sa.text(f"principal_type = '{principal}' AND scope_type = 'table'"),
        )


def downgrade() -> None:
    op.drop_table("connection_access_grants")
