"""Typed, temporary queries over one immutable dataset snapshot."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID


class SnapshotFilterOperator(StrEnum):
    EQUALS = "eq"
    NOT_EQUALS = "neq"
    GREATER_THAN = "gt"
    GREATER_THAN_OR_EQUAL = "gte"
    LESS_THAN = "lt"
    LESS_THAN_OR_EQUAL = "lte"
    BETWEEN = "between"
    IN = "in"
    CONTAINS = "contains"
    STARTS_WITH = "starts_with"
    IS_NULL = "is_null"
    IS_NOT_NULL = "is_not_null"


class SnapshotSortDirection(StrEnum):
    ASCENDING = "asc"
    DESCENDING = "desc"


class SnapshotRefreshState(StrEnum):
    IDLE = "idle"
    QUEUED = "queued"
    RUNNING = "running"


@dataclass(frozen=True, slots=True)
class SnapshotQueryField:
    field_id: UUID
    name: str
    storage_name: str
    data_type: str
    nullable: bool
    visible: bool


@dataclass(frozen=True, slots=True)
class SnapshotQueryFilter:
    field_id: UUID
    operator: SnapshotFilterOperator
    value: Any = None
    values: tuple[Any, ...] = ()


@dataclass(frozen=True, slots=True)
class SnapshotQuerySort:
    field_id: UUID
    direction: SnapshotSortDirection


@dataclass(frozen=True, slots=True)
class SnapshotQuery:
    field_ids: tuple[UUID, ...]
    filters: tuple[SnapshotQueryFilter, ...]
    sorts: tuple[SnapshotQuerySort, ...]
    page_size: int
    cursor: str | None


@dataclass(frozen=True, slots=True)
class ActiveSnapshotQueryContext:
    dataset_id: UUID
    snapshot_id: UUID
    storage_backend: str
    object_key: str
    activated_at: datetime
    snapshot_row_count: int
    fields: tuple[SnapshotQueryField, ...]
    refresh_state: SnapshotRefreshState
    last_refresh_error: str | None
    is_stale: bool


@dataclass(frozen=True, slots=True)
class SnapshotQueryCursorPosition:
    sort_values: tuple[Any, ...]
    file_row_number: int


@dataclass(frozen=True, slots=True)
class SnapshotQueryExecution:
    rows: tuple[dict[str, Any], ...]
    next_position: SnapshotQueryCursorPosition | None
    truncated_by_bytes: bool


@dataclass(frozen=True, slots=True)
class SnapshotQueryPage:
    columns: tuple[SnapshotQueryField, ...]
    rows: tuple[dict[str, Any], ...]
    snapshot_id: UUID
    snapshot_row_count: int
    snapshot_activated_at: datetime
    refresh_state: SnapshotRefreshState
    last_refresh_error: str | None
    is_stale: bool
    next_cursor: str | None
    truncated_by_bytes: bool
