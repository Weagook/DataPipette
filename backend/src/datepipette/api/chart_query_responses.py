"""Shared API representations for saved-chart query results."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from datepipette.domain.charts import (
    ChartQueryResult,
    ChartResultColumnRole,
    ChartTimeBucket,
)
from datepipette.domain.snapshot_queries import SnapshotRefreshState


class ChartResultColumnResponse(BaseModel):
    key: str
    role: ChartResultColumnRole
    field_id: UUID
    measure_id: UUID | None
    label: str
    data_type: str
    time_bucket: ChartTimeBucket | None
    timezone: str | None


class ChartQueryDataResponse(BaseModel):
    columns: list[ChartResultColumnResponse]
    rows: list[dict[str, Any]]


class ChartQueryMetaResponse(BaseModel):
    chart_id: UUID
    chart_revision: int
    dataset_id: UUID
    snapshot_id: UUID
    snapshot_activated_at: datetime
    refresh_state: SnapshotRefreshState
    last_refresh_error: str | None
    last_refresh_error_at: datetime | None
    is_stale: bool
    truncated_by_bytes: bool


class ChartQueryResponse(BaseModel):
    data: ChartQueryDataResponse
    meta: ChartQueryMetaResponse


def chart_query_data_response(result: ChartQueryResult) -> ChartQueryDataResponse:
    return ChartQueryDataResponse(
        columns=[
            ChartResultColumnResponse(
                key=item.key,
                role=item.role,
                field_id=item.field_id,
                measure_id=item.measure_id,
                label=item.label,
                data_type=item.data_type,
                time_bucket=item.time_bucket,
                timezone=item.timezone,
            )
            for item in result.columns
        ],
        rows=list(result.rows),
    )


def chart_query_meta_response(result: ChartQueryResult) -> ChartQueryMetaResponse:
    return ChartQueryMetaResponse(
        chart_id=result.chart_id,
        chart_revision=result.chart_revision,
        dataset_id=result.dataset_id,
        snapshot_id=result.snapshot_id,
        snapshot_activated_at=result.snapshot_activated_at,
        refresh_state=result.refresh_state,
        last_refresh_error=result.last_refresh_error,
        last_refresh_error_at=result.last_refresh_error_at,
        is_stale=result.is_stale,
        truncated_by_bytes=result.truncated_by_bytes,
    )


def chart_query_response(result: ChartQueryResult) -> ChartQueryResponse:
    return ChartQueryResponse(
        data=chart_query_data_response(result),
        meta=chart_query_meta_response(result),
    )
