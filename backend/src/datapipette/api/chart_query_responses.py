"""Shared API representations for saved-chart query results."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from datapipette.domain.charts import (
    ChartPreviewResult,
    ChartQueryResult,
    ChartResultColumnRole,
    ChartTimeBucket,
    ChartType,
)
from datapipette.domain.snapshot_queries import SnapshotRefreshState


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
    policy_revision: int
    chart_id: UUID
    chart_name: str
    chart_type: ChartType
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


class ChartPreviewMetaResponse(BaseModel):
    policy_revision: int
    chart_type: ChartType
    dataset_id: UUID
    snapshot_id: UUID
    snapshot_activated_at: datetime
    refresh_state: SnapshotRefreshState
    is_stale: bool
    truncated_by_bytes: bool


class ChartPreviewResponse(BaseModel):
    data: ChartQueryDataResponse
    meta: ChartPreviewMetaResponse


def chart_query_data_response(
    result: ChartQueryResult | ChartPreviewResult,
) -> ChartQueryDataResponse:
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
        policy_revision=result.policy_revision,
        chart_id=result.chart_id,
        chart_name=result.chart_name,
        chart_type=result.chart_type,
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


def chart_preview_response(result: ChartPreviewResult) -> ChartPreviewResponse:
    return ChartPreviewResponse(
        data=chart_query_data_response(result),
        meta=ChartPreviewMetaResponse(
            policy_revision=result.policy_revision,
            chart_type=result.chart_type,
            dataset_id=result.dataset_id,
            snapshot_id=result.snapshot_id,
            snapshot_activated_at=result.snapshot_activated_at,
            refresh_state=result.refresh_state,
            is_stale=result.is_stale,
            truncated_by_bytes=result.truncated_by_bytes,
        ),
    )
