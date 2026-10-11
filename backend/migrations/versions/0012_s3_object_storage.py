"""Add durable S3-compatible object storage backends.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_dataset_snapshots_storage_backend_valid",
        "dataset_snapshots",
        type_="check",
    )
    op.create_check_constraint(
        "ck_dataset_snapshots_storage_backend_valid",
        "dataset_snapshots",
        "storage_backend IN ('local', 's3')",
    )
    op.add_column(
        "dataset_exports",
        sa.Column("storage_backend", sa.String(length=20), nullable=True),
    )
    op.create_check_constraint(
        "ck_dataset_exports_storage_backend_valid",
        "dataset_exports",
        "storage_backend IS NULL OR storage_backend IN ('local', 's3')",
    )
    op.execute("UPDATE dataset_exports SET storage_backend = 'local' WHERE object_key IS NOT NULL")
    op.create_check_constraint(
        "ck_dataset_exports_storage_object_shape_valid",
        "dataset_exports",
        "(object_key IS NULL AND storage_backend IS NULL) OR "
        "(object_key IS NOT NULL AND storage_backend IS NOT NULL)",
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM dataset_snapshots WHERE storage_backend <> 'local')
               OR EXISTS (
                   SELECT 1 FROM dataset_exports
                   WHERE storage_backend IS NOT NULL AND storage_backend <> 'local'
               ) THEN
                RAISE EXCEPTION 'Cannot downgrade while S3 analytical objects exist';
            END IF;
        END
        $$
        """
    )
    op.drop_constraint(
        "ck_dataset_exports_storage_object_shape_valid",
        "dataset_exports",
        type_="check",
    )
    op.drop_constraint(
        "ck_dataset_exports_storage_backend_valid",
        "dataset_exports",
        type_="check",
    )
    op.drop_column("dataset_exports", "storage_backend")
    op.drop_constraint(
        "ck_dataset_snapshots_storage_backend_valid",
        "dataset_snapshots",
        type_="check",
    )
    op.create_check_constraint(
        "ck_dataset_snapshots_storage_backend_valid",
        "dataset_snapshots",
        "storage_backend IN ('local')",
    )
