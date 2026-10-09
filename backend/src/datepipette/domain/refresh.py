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


class RefreshClaimOutcome(StrEnum):
    CLAIMED = "claimed"
    DEFERRED = "deferred"
    TERMINAL = "terminal"
    NOT_FOUND = "not_found"


@dataclass(frozen=True, slots=True)
class RefreshRun:
    id: UUID
    dataset_id: UUID
    dataset_version_id: UUID
    requested_revision: int
    state: RefreshRunState
    requested_by: UUID
    dispatch_attempts: int
    dispatched_at: datetime | None
    attempt_count: int
    error_code: str | None
    requested_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class RefreshRequestOutcome:
    run: RefreshRun
    coalesced: bool
    dispatch_required: bool
