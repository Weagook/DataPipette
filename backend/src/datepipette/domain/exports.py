"""Durable dataset export vocabulary."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from datepipette.domain.snapshot_queries import SnapshotQuery


class ExportFormat(StrEnum):
    CSV = "csv"
    XLSX = "xlsx"


class ExportState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ExportClaimOutcome(StrEnum):
    CLAIMED = "claimed"
    TERMINAL = "terminal"
    NOT_FOUND = "not_found"


@dataclass(frozen=True, slots=True)
class ExportJob:
    id: UUID
    dataset_id: UUID
    snapshot_id: UUID
    requested_by: UUID
    format: ExportFormat
    state: ExportState
    query: SnapshotQuery
    dispatch_attempts: int
    dispatched_at: datetime | None
    attempt_count: int
    object_key: str | None
    row_count: int | None
    byte_size: int | None
    checksum_sha256: str | None
    content_type: str | None
    error_code: str | None
    requested_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    expires_at: datetime
    updated_at: datetime
    storage_backend: str | None = None


@dataclass(frozen=True, slots=True)
class ExportRequestOutcome:
    job: ExportJob
    replayed: bool
    dispatch_required: bool


@dataclass(frozen=True, slots=True)
class ExportArtifact:
    object_key: str
    row_count: int
    byte_size: int
    checksum_sha256: str
    content_type: str
    storage_backend: str = "local"


@dataclass(frozen=True, slots=True)
class ExportDownload:
    path: str | None
    content_type: str
    filename: str
    redirect_url: str | None = None
