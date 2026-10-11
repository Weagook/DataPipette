"""Track analytical object lifecycle cleanup.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "dataset_snapshots",
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "dataset_snapshots",
        sa.Column("cleanup_claimed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_dataset_snapshots_purged_only_when_superseded",
        "dataset_snapshots",
        "purged_at IS NULL OR state = 'superseded'",
    )
    op.create_index(
        "ix_dataset_snapshots_cleanup",
        "dataset_snapshots",
        ["superseded_at"],
        unique=False,
        postgresql_where=sa.text("state = 'superseded' AND purged_at IS NULL"),
    )
    op.add_column(
        "dataset_exports",
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "dataset_exports",
        sa.Column("cleanup_claimed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_dataset_exports_cleanup",
        "dataset_exports",
        ["expires_at"],
        unique=False,
        postgresql_where=sa.text("object_key IS NOT NULL AND purged_at IS NULL"),
    )
    op.create_table(
        "object_cleanup_cursors",
        sa.Column("storage_backend", sa.String(length=20), nullable=False),
        sa.Column("prefix", sa.String(length=20), nullable=False),
        sa.Column("after_key", sa.String(length=1024), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "storage_backend IN ('local', 's3')",
            name=op.f("ck_object_cleanup_cursors_backend_valid"),
        ),
        sa.CheckConstraint(
            "prefix IN ('datasets/', 'exports/')",
            name=op.f("ck_object_cleanup_cursors_prefix_valid"),
        ),
        sa.PrimaryKeyConstraint(
            "storage_backend",
            "prefix",
            name=op.f("pk_object_cleanup_cursors"),
        ),
    )


def downgrade() -> None:
    op.drop_table("object_cleanup_cursors")
    op.drop_index("ix_dataset_exports_cleanup", table_name="dataset_exports")
    op.drop_column("dataset_exports", "cleanup_claimed_at")
    op.drop_column("dataset_exports", "purged_at")
    op.drop_index("ix_dataset_snapshots_cleanup", table_name="dataset_snapshots")
    op.drop_constraint(
        "ck_dataset_snapshots_purged_only_when_superseded",
        "dataset_snapshots",
        type_="check",
    )
    op.drop_column("dataset_snapshots", "cleanup_claimed_at")
    op.drop_column("dataset_snapshots", "purged_at")
