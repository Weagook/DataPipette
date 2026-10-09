"""Add encrypted PostgreSQL source connections.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "postgresql_connections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("host", sa.String(length=253), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("database_name", sa.String(length=255), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=False),
        sa.Column("encrypted_password", sa.Text(), nullable=False),
        sa.Column("encrypted_ca_certificate", sa.Text(), nullable=True),
        sa.Column("ssl_mode", sa.String(length=20), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_succeeded", sa.Boolean(), nullable=True),
        sa.Column("last_test_error_code", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "port BETWEEN 1 AND 65535",
            name="ck_postgresql_connections_port_valid",
        ),
        sa.CheckConstraint(
            "ssl_mode IN ('disable', 'require', 'verify-ca', 'verify-full')",
            name="ck_postgresql_connections_ssl_mode_valid",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_postgresql_connections_created_by_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_postgresql_connections"),
        sa.UniqueConstraint("name", name="uq_postgresql_connections_name"),
    )


def downgrade() -> None:
    op.drop_table("postgresql_connections")
