"""Durable dataset refresh-run vocabulary."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class RefreshRunState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RefreshTriggerType(StrEnum):
    MANUAL = "manual"
    SCHEDULED = "scheduled"


class RefreshClaimOutcome(StrEnum):
    CLAIMED = "claimed"
    DEFERRED = "deferred"
    TERMINAL = "terminal"
    NOT_FOUND = "not_found"


class RefreshCancelOutcome(StrEnum):
    CANCELLED = "cancelled"
    REQUESTED = "requested"
    ALREADY_CANCELLED = "already_cancelled"
    CONFLICT = "conflict"
    NOT_FOUND = "not_found"


@dataclass(frozen=True, slots=True)
class RefreshRun:
    id: UUID
    dataset_id: UUID
    dataset_version_id: UUID
    requested_revision: int
    state: RefreshRunState
    requested_by: UUID
    trigger_type: RefreshTriggerType
    schedule_id: UUID | None
    dispatch_attempts: int
    dispatched_at: datetime | None
    attempt_count: int
    error_code: str | None
    next_attempt_at: datetime | None
    cancel_requested_at: datetime | None
    cancelled_at: datetime | None
    lease_expires_at: datetime | None
    snapshot_id: UUID | None
    requested_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime
    policy_revision: int | None = None


@dataclass(frozen=True, slots=True)
class RefreshRequestOutcome:
    run: RefreshRun
    coalesced: bool
    dispatch_required: bool
