"""Add service accounts and scoped API tokens.

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("account_type", sa.String(length=20), nullable=False, server_default="human"),
    )
    op.create_check_constraint(
        "account_type_valid",
        "users",
        "account_type IN ('human', 'service')",
    )
    op.alter_column("users", "account_type", server_default=None)
    op.create_index(
        "uq_service_accounts_name_normalized",
        "users",
        [sa.text("lower(display_name)")],
        unique=True,
        postgresql_where=sa.text("account_type = 'service'"),
    )

    op.drop_constraint("ck_audit_events_actor_type_valid", "audit_events", type_="check")
    op.create_check_constraint(
        "actor_type_valid",
        "audit_events",
        "actor_type IN ('user', 'service_account', 'anonymous', 'system')",
    )

    op.create_table(
        "api_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("service_account_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "scopes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("jsonb_typeof(scopes) = 'array'", name="scopes_array"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["service_account_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(
        "ix_api_tokens_service_account_id",
        "api_tokens",
        ["service_account_id"],
        unique=False,
    )
    op.create_index(
        "ix_api_tokens_expires_at",
        "api_tokens",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_api_tokens_expires_at", table_name="api_tokens")
    op.drop_index("ix_api_tokens_service_account_id", table_name="api_tokens")
    op.drop_table("api_tokens")
    # Earlier schemas cannot distinguish or safely authenticate non-human accounts.
    # Deleting them also cascades their memberships and direct ACL grants.
    op.execute("DELETE FROM users WHERE account_type = 'service'")
    op.drop_constraint("actor_type_valid", "audit_events", type_="check")
    op.create_check_constraint(
        "ck_audit_events_actor_type_valid",
        "audit_events",
        "actor_type IN ('user', 'anonymous', 'system')",
    )
    op.drop_constraint("account_type_valid", "users", type_="check")
    op.drop_index("uq_service_accounts_name_normalized", table_name="users")
    op.drop_column("users", "account_type")
