"""SQLAlchemy models owned by the DatePipette metadata database."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
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


class PostgresConnectionModel(Base):
    __tablename__ = "postgresql_connections"
    __table_args__ = (
        CheckConstraint("port BETWEEN 1 AND 65535", name="port_valid"),
        CheckConstraint(
            "ssl_mode IN ('disable', 'require', 'verify-ca', 'verify-full')",
            name="ssl_mode_valid",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    host: Mapped[str] = mapped_column(String(253))
    port: Mapped[int] = mapped_column(Integer)
    database_name: Mapped[str] = mapped_column(String(255))
    username: Mapped[str] = mapped_column(String(255))
    encrypted_password: Mapped[str] = mapped_column(Text)
    encrypted_ca_certificate: Mapped[str | None] = mapped_column(Text)
    ssl_mode: Mapped[str] = mapped_column(String(20))
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_succeeded: Mapped[bool | None] = mapped_column(Boolean)
    last_test_error_code: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DatasetModel(Base):
    __tablename__ = "datasets"
    __table_args__ = (
        Index(
            "uq_datasets_owner_name_normalized",
            "owner_id",
            text("lower(name)"),
            unique=True,
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("postgresql_connections.id", ondelete="RESTRICT")
    )
    active_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "dataset_versions.id",
            name="fk_datasets_active_version_id_dataset_versions",
            ondelete="RESTRICT",
            use_alter=True,
        )
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DatasetVersionModel(Base):
    __tablename__ = "dataset_versions"
    __table_args__ = (
        CheckConstraint(
            "state IN ('draft', 'published', 'superseded')",
            name="state_valid",
        ),
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint("jsonb_typeof(definition) = 'object'", name="definition_object"),
        CheckConstraint(
            "validation IS NULL OR jsonb_typeof(validation) = 'object'",
            name="validation_object",
        ),
        Index(
            "uq_dataset_versions_one_draft",
            "dataset_id",
            unique=True,
            postgresql_where=text("state = 'draft'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    dataset_id: Mapped[UUID] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"),
        index=True,
    )
    state: Mapped[str] = mapped_column(String(20))
    revision: Mapped[int] = mapped_column(Integer)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB)
    validation: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DatasetRefreshRunModel(Base):
    __tablename__ = "dataset_refresh_runs"
    __table_args__ = (
        CheckConstraint(
            "state IN ('queued', 'running', 'succeeded', 'failed')",
            name="state_valid",
        ),
        CheckConstraint("requested_revision >= 1", name="requested_revision_positive"),
        CheckConstraint("dispatch_attempts >= 0", name="dispatch_attempts_nonnegative"),
        CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
        Index(
            "uq_dataset_refresh_runs_one_queued",
            "dataset_id",
            unique=True,
            postgresql_where=text("state = 'queued'"),
        ),
        Index(
            "uq_dataset_refresh_runs_one_running",
            "dataset_id",
            unique=True,
            postgresql_where=text("state = 'running'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    dataset_id: Mapped[UUID] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"),
        index=True,
    )
    dataset_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="RESTRICT")
    )
    requested_revision: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(20))
    requested_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    dispatch_attempts: Mapped[int] = mapped_column(Integer)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(50))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DatasetRefreshRequestModel(Base):
    __tablename__ = "dataset_refresh_requests"
    __table_args__ = (
        Index(
            "uq_dataset_refresh_requests_actor_dataset_key",
            "actor_id",
            "dataset_id",
            "idempotency_key_hash",
            unique=True,
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    dataset_id: Mapped[UUID] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"),
        index=True,
    )
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    idempotency_key_hash: Mapped[str] = mapped_column(String(64))
    refresh_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("dataset_refresh_runs.id", ondelete="CASCADE"),
        index=True,
    )
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
