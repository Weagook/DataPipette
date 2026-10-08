"""SQLAlchemy models owned by the DatePipette metadata database."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from datepipette.infrastructure.database import Base


class UserModel(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("email = lower(email)", name="email_normalized"),
        CheckConstraint("role IN ('admin', 'analyst', 'viewer')", name="role_valid"),
        CheckConstraint("status IN ('active', 'disabled')", name="status_valid"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    display_name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BrowserSessionModel(Base):
    __tablename__ = "browser_sessions"
    __table_args__ = (Index("ix_browser_sessions_user_expires", "user_id", "expires_at"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditEventModel(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint(
            "actor_type IN ('user', 'anonymous', 'system')",
            name="actor_type_valid",
        ),
        CheckConstraint(
            "outcome IN ('success', 'failure', 'throttled')",
            name="outcome_valid",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    actor_type: Mapped[str] = mapped_column(String(20))
    actor_id: Mapped[UUID | None]
    action: Mapped[str] = mapped_column(String(100), index=True)
    outcome: Mapped[str] = mapped_column(String(20))
    resource_type: Mapped[str | None] = mapped_column(String(50))
    resource_id: Mapped[UUID | None]
    request_id: Mapped[str | None] = mapped_column(String(128), index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
