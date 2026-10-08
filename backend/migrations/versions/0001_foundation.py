"""Establish the metadata migration chain.

Revision ID: 0001
Revises:
Create Date: 2026-10-08
"""

from collections.abc import Sequence

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create no business tables until their owning feature is implemented."""


def downgrade() -> None:
    """Remove no business tables from the empty foundation revision."""
