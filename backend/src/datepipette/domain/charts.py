"""Persistent analytical chart specifications and result vocabulary."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from datepipette.domain.snapshot_queries import (
    SnapshotQueryFilter,
    SnapshotRefreshState,
    SnapshotSortDirection,
)

MAX_CHART_DIMENSIONS = 1
MAX_CHART_MEASURES = 5
MAX_CHART_FILTERS = 20
MAX_CHART_SORTS = 5
MAX_CHART_RESULT_ROWS = 1000


class ChartType(StrEnum):
    BAR = "bar"
    LINE = "line"
    PIE = "pie"
    DONUT = "donut"
    KPI = "kpi"


class ChartTimeBucket(StrEnum):
    HOUR = "hour"
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"


class ChartAggregation(StrEnum):
    COUNT = "count"
    COUNT_DISTINCT = "count_distinct"
    SUM = "sum"
    AVERAGE = "avg"
    MINIMUM = "min"
    MAXIMUM = "max"


class ChartSortTargetType(StrEnum):
    DIMENSION = "dimension"
    MEASURE = "measure"


class ChartResultColumnRole(StrEnum):
    DIMENSION = "dimension"
    MEASURE = "measure"


@dataclass(frozen=True, slots=True)
class ChartDimension:
    field_id: UUID
    time_bucket: ChartTimeBucket | None = None


@dataclass(frozen=True, slots=True)
class ChartMeasure:
    id: UUID
    field_id: UUID
    aggregation: ChartAggregation
    label: str

    def __post_init__(self) -> None:
        normalized = " ".join(self.label.split())
        if not normalized:
            raise ValueError("Chart measure label must not be empty")
        if len(normalized) > 100:
            raise ValueError("Chart measure label must contain at most 100 characters")
        object.__setattr__(self, "label", normalized)


@dataclass(frozen=True, slots=True)
class ChartSort:
    target_type: ChartSortTargetType
    target_id: UUID
    direction: SnapshotSortDirection


@dataclass(frozen=True, slots=True)
class ChartDefinition:
    chart_type: ChartType
    dimensions: tuple[ChartDimension, ...]
    measures: tuple[ChartMeasure, ...]
    filters: tuple[SnapshotQueryFilter, ...] = ()
    sorts: tuple[ChartSort, ...] = ()
    limit: int = 100
    schema_version: int = 2

    def __post_init__(self) -> None:
        if self.schema_version not in {1, 2}:
            raise ValueError("Unsupported chart definition schema version")
        if self.schema_version == 1 and any(
            item.time_bucket is not None for item in self.dimensions
        ):
            raise ValueError("Chart definition schema version 1 does not support time buckets")
        expected_dimensions = 0 if self.chart_type is ChartType.KPI else 1
        if len(self.dimensions) != expected_dimensions:
            raise ValueError(
                "KPI charts require no dimensions; other chart types require one dimension"
            )
        if not 1 <= len(self.measures) <= MAX_CHART_MEASURES:
            raise ValueError(f"Charts require between 1 and {MAX_CHART_MEASURES} measures")
        if (
            self.chart_type in {ChartType.PIE, ChartType.DONUT, ChartType.KPI}
            and len(self.measures) != 1
        ):
            raise ValueError("Pie, donut, and KPI charts require exactly one measure")
        if len(self.filters) > MAX_CHART_FILTERS:
            raise ValueError("Chart contains too many filters")
        if len(self.sorts) > MAX_CHART_SORTS:
            raise ValueError("Chart contains too many sorts")
        if not 1 <= self.limit <= MAX_CHART_RESULT_ROWS:
            raise ValueError("Chart result limit is outside the supported range")
        dimension_ids = [item.field_id for item in self.dimensions]
        if len(dimension_ids) != len(set(dimension_ids)):
            raise ValueError("Chart dimensions must be unique")
        measure_ids = [item.id for item in self.measures]
        if len(measure_ids) != len(set(measure_ids)):
            raise ValueError("Chart measure identifiers must be unique")
        sort_targets = [(item.target_type, item.target_id) for item in self.sorts]
        if len(sort_targets) != len(set(sort_targets)):
            raise ValueError("Chart sort targets must be unique")

    @property
    def referenced_field_ids(self) -> frozenset[UUID]:
        return frozenset(
            [item.field_id for item in self.dimensions]
            + [item.field_id for item in self.measures]
            + [item.field_id for item in self.filters]
        )

    def to_document(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "chart_type": self.chart_type.value,
            "dimensions": [
                {
                    "field_id": str(item.field_id),
                    **(
                        {"time_bucket": item.time_bucket.value}
                        if item.time_bucket is not None
                        else {}
                    ),
                }
                for item in self.dimensions
            ],
            "measures": [
                {
                    "id": str(item.id),
                    "field_id": str(item.field_id),
                    "aggregation": item.aggregation.value,
                    "label": item.label,
                }
                for item in self.measures
            ],
            "filters": [
                {
                    "field_id": str(item.field_id),
                    "operator": item.operator.value,
                    "value": item.value,
                    "values": list(item.values),
                }
                for item in self.filters
            ],
            "sorts": [
                {
                    "target_type": item.target_type.value,
                    "target_id": str(item.target_id),
                    "direction": item.direction.value,
                }
                for item in self.sorts
            ],
            "limit": self.limit,
        }

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> "ChartDefinition":
        from datepipette.domain.snapshot_queries import SnapshotFilterOperator

        try:
            return cls(
                schema_version=document["schema_version"],
                chart_type=ChartType(document["chart_type"]),
                dimensions=tuple(
                    ChartDimension(
                        UUID(item["field_id"]),
                        (
                            ChartTimeBucket(item["time_bucket"])
                            if item.get("time_bucket") is not None
                            else None
                        ),
                    )
                    for item in document["dimensions"]
                ),
                measures=tuple(
                    ChartMeasure(
                        id=UUID(item["id"]),
                        field_id=UUID(item["field_id"]),
                        aggregation=ChartAggregation(item["aggregation"]),
                        label=item["label"],
                    )
                    for item in document["measures"]
                ),
                filters=tuple(
                    SnapshotQueryFilter(
                        field_id=UUID(item["field_id"]),
                        operator=SnapshotFilterOperator(item["operator"]),
                        value=item.get("value"),
                        values=tuple(item.get("values", [])),
                    )
                    for item in document["filters"]
                ),
                sorts=tuple(
                    ChartSort(
                        target_type=ChartSortTargetType(item["target_type"]),
                        target_id=UUID(item["target_id"]),
                        direction=SnapshotSortDirection(item["direction"]),
                    )
                    for item in document["sorts"]
                ),
                limit=document["limit"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Stored chart definition is invalid") from exc


@dataclass(frozen=True, slots=True)
class Chart:
    id: UUID
    dataset_id: UUID
    owner_id: UUID
    name: str
    revision: int
    definition: ChartDefinition
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ChartResultColumn:
    key: str
    role: ChartResultColumnRole
    field_id: UUID
    measure_id: UUID | None
    label: str
    data_type: str
    time_bucket: ChartTimeBucket | None = None
    timezone: str | None = None


@dataclass(frozen=True, slots=True)
class ChartQueryExecution:
    columns: tuple[ChartResultColumn, ...]
    rows: tuple[dict[str, Any], ...]
    truncated_by_bytes: bool


@dataclass(frozen=True, slots=True)
class ChartQueryResult:
    policy_revision: int
    chart_id: UUID
    chart_revision: int
    dataset_id: UUID
    snapshot_id: UUID
    snapshot_activated_at: datetime
    refresh_state: SnapshotRefreshState
    last_refresh_error: str | None
    last_refresh_error_at: datetime | None
    is_stale: bool
    columns: tuple[ChartResultColumn, ...]
    rows: tuple[dict[str, Any], ...]
    truncated_by_bytes: bool
