"""Add immutable dataset snapshots and active snapshot references.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dataset_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_version_id", sa.Uuid(), nullable=False),
        sa.Column("refresh_run_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("storage_backend", sa.String(length=20), nullable=False),
        sa.Column("object_key", sa.String(length=1024), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "schema_document",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "byte_size > 0",
            name="ck_dataset_snapshots_byte_size_positive",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(schema_document) = 'object'",
            name="ck_dataset_snapshots_schema_object",
        ),
        sa.CheckConstraint(
            "row_count >= 0",
            name="ck_dataset_snapshots_row_count_nonnegative",
        ),
        sa.CheckConstraint(
            "state IN ('active', 'superseded')",
            name="ck_dataset_snapshots_state_valid",
        ),
        sa.CheckConstraint(
            "storage_backend IN ('local')",
            name="ck_dataset_snapshots_storage_backend_valid",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["datasets.id"],
            name="fk_dataset_snapshots_dataset_id_datasets",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_version_id"],
            ["dataset_versions.id"],
            name="fk_dataset_snapshots_dataset_version_id_dataset_versions",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["refresh_run_id"],
            ["dataset_refresh_runs.id"],
            name="fk_dataset_snapshots_refresh_run_id_dataset_refresh_runs",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_dataset_snapshots"),
        sa.UniqueConstraint(
            "object_key",
            name="uq_dataset_snapshots_object_key",
        ),
        sa.UniqueConstraint(
            "refresh_run_id",
            name="uq_dataset_snapshots_refresh_run_id",
        ),
    )
    op.create_index(
        "ix_dataset_snapshots_dataset_id",
        "dataset_snapshots",
        ["dataset_id"],
    )
    op.create_index(
        "ix_dataset_snapshots_dataset_version_id",
        "dataset_snapshots",
        ["dataset_version_id"],
    )
    op.create_index(
        "uq_dataset_snapshots_one_active",
        "dataset_snapshots",
        ["dataset_id"],
        unique=True,
        postgresql_where=sa.text("state = 'active'"),
    )
    op.add_column("datasets", sa.Column("active_snapshot_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_datasets_active_snapshot_id_dataset_snapshots",
        "datasets",
        "dataset_snapshots",
        ["active_snapshot_id"],
        ["id"],
        ondelete="RESTRICT",
        use_alter=True,
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_datasets_active_snapshot_id_dataset_snapshots",
        "datasets",
        type_="foreignkey",
    )
    op.drop_column("datasets", "active_snapshot_id")
    op.drop_index("uq_dataset_snapshots_one_active", table_name="dataset_snapshots")
    op.drop_index("ix_dataset_snapshots_dataset_version_id", table_name="dataset_snapshots")
    op.drop_index("ix_dataset_snapshots_dataset_id", table_name="dataset_snapshots")
    op.drop_table("dataset_snapshots")
