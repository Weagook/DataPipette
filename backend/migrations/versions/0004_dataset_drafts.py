"""Add versioned visual dataset drafts.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "datasets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("active_version_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["postgresql_connections.id"],
            name="fk_datasets_connection_id_postgresql_connections",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name="fk_datasets_owner_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_datasets"),
    )
    op.create_index(
        "uq_datasets_owner_name_normalized",
        "datasets",
        ["owner_id", sa.text("lower(name)")],
        unique=True,
    )
    op.create_table(
        "dataset_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column(
            "definition",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "jsonb_typeof(definition) = 'object'",
            name="ck_dataset_versions_definition_object",
        ),
        sa.CheckConstraint(
            "revision >= 1",
            name="ck_dataset_versions_revision_positive",
        ),
        sa.CheckConstraint(
            "state IN ('draft', 'published', 'superseded')",
            name="ck_dataset_versions_state_valid",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_dataset_versions_created_by_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["datasets.id"],
            name="fk_dataset_versions_dataset_id_datasets",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_dataset_versions"),
    )
    op.create_index(
        "ix_dataset_versions_dataset_id",
        "dataset_versions",
        ["dataset_id"],
    )
    op.create_index(
        "uq_dataset_versions_one_draft",
        "dataset_versions",
        ["dataset_id"],
        unique=True,
        postgresql_where=sa.text("state = 'draft'"),
    )
    op.create_foreign_key(
        "fk_datasets_active_version_id_dataset_versions",
        "datasets",
        "dataset_versions",
        ["active_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_datasets_active_version_id_dataset_versions",
        "datasets",
        type_="foreignkey",
    )
    op.drop_index("uq_dataset_versions_one_draft", table_name="dataset_versions")
    op.drop_index("ix_dataset_versions_dataset_id", table_name="dataset_versions")
    op.drop_table("dataset_versions")
    op.drop_index("uq_datasets_owner_name_normalized", table_name="datasets")
    op.drop_table("datasets")
