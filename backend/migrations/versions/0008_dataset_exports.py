"""Add durable dataset export jobs.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dataset_exports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("format", sa.String(length=10), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("query_document", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("dispatch_attempts", sa.Integer(), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("object_key", sa.String(length=1024), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("byte_size", sa.Integer(), nullable=True),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=True),
        sa.Column("content_type", sa.String(length=100), nullable=True),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("format IN ('csv', 'xlsx')", name="ck_dataset_exports_format_valid"),
        sa.CheckConstraint(
            "state IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_dataset_exports_state_valid",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(query_document) = 'object'", name="ck_dataset_exports_query_object"
        ),
        sa.CheckConstraint(
            "dispatch_attempts >= 0", name="ck_dataset_exports_dispatch_attempts_nonnegative"
        ),
        sa.CheckConstraint(
            "attempt_count >= 0", name="ck_dataset_exports_attempt_count_nonnegative"
        ),
        sa.CheckConstraint(
            "row_count IS NULL OR row_count >= 0", name="ck_dataset_exports_row_count_nonnegative"
        ),
        sa.CheckConstraint(
            "byte_size IS NULL OR byte_size > 0", name="ck_dataset_exports_byte_size_positive"
        ),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["snapshot_id"], ["dataset_snapshots.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("object_key"),
    )
    op.create_index("ix_dataset_exports_dataset_id", "dataset_exports", ["dataset_id"])
    op.create_index("ix_dataset_exports_snapshot_id", "dataset_exports", ["snapshot_id"])
    op.create_index(
        "ix_dataset_exports_dataset_requested", "dataset_exports", ["dataset_id", "requested_at"]
    )
    op.create_index("ix_dataset_exports_expiry", "dataset_exports", ["expires_at"])
    op.create_table(
        "dataset_export_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("export_id", sa.Uuid(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["export_id"], ["dataset_exports.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_dataset_export_requests_dataset_id", "dataset_export_requests", ["dataset_id"]
    )
    op.create_index(
        "ix_dataset_export_requests_export_id", "dataset_export_requests", ["export_id"]
    )
    op.create_index(
        "uq_dataset_export_requests_actor_dataset_key",
        "dataset_export_requests",
        ["actor_id", "dataset_id", "idempotency_key_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("dataset_export_requests")
    op.drop_table("dataset_exports")
