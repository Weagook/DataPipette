"""Read-only typed queries over active dataset snapshots."""

from datetime import datetime
from typing import Any, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from datepipette.api.dependencies.authentication import CurrentSession, request_id
from datepipette.api.problems import ApiProblem, ProblemDetails
from datepipette.application.snapshot_queries import (
    MAX_QUERY_CURSOR_LENGTH,
    MAX_QUERY_FIELDS,
    MAX_QUERY_FILTERS,
    MAX_QUERY_IN_VALUES,
    MAX_QUERY_PAGE_ROWS,
    MAX_QUERY_SORTS,
    DatasetSnapshotNotFoundError,
    DatasetSnapshotNotPublishedError,
    DatasetSnapshotQueryService,
    DatasetSnapshotQueryTimeoutError,
    DatasetSnapshotResultTooLargeError,
    DatasetSnapshotUnavailableError,
    InvalidSnapshotCursorError,
    InvalidSnapshotQueryError,
)
from datepipette.domain.snapshot_queries import (
    DatasetDataState,
    SnapshotFilterOperator,
    SnapshotQuery,
    SnapshotQueryFilter,
    SnapshotQuerySort,
    SnapshotRefreshState,
    SnapshotSortDirection,
)

router = APIRouter(prefix="/datasets", tags=["dataset rows"])


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SnapshotFilterPayload(StrictModel):
    field_id: UUID
    operator: SnapshotFilterOperator
    value: Any = None
    values: list[Any] = Field(default_factory=list, max_length=MAX_QUERY_IN_VALUES)


class SnapshotSortPayload(StrictModel):
    field_id: UUID
    direction: SnapshotSortDirection = SnapshotSortDirection.ASCENDING


class SnapshotRowsQueryRequest(StrictModel):
    fields: list[UUID] = Field(min_length=1, max_length=MAX_QUERY_FIELDS)
    filters: list[SnapshotFilterPayload] = Field(
        default_factory=list,
        max_length=MAX_QUERY_FILTERS,
    )
    sorts: list[SnapshotSortPayload] = Field(
        default_factory=list,
        max_length=MAX_QUERY_SORTS,
    )
    page_size: int = Field(default=100, ge=1, le=MAX_QUERY_PAGE_ROWS)
    cursor: str | None = Field(default=None, max_length=MAX_QUERY_CURSOR_LENGTH)


class SnapshotColumnResponse(BaseModel):
    field_id: UUID
    name: str
    data_type: str
    nullable: bool


class SnapshotRowsDataResponse(BaseModel):
    columns: list[SnapshotColumnResponse]
    rows: list[dict[str, Any]]


class SnapshotRowsMetaResponse(BaseModel):
    snapshot_id: UUID
    snapshot_state: Literal["ready"] = "ready"
    snapshot_row_count: int
    last_success_at: datetime
    refresh_state: SnapshotRefreshState
    last_refresh_error: str | None
    last_refresh_error_at: datetime | None
    is_stale: bool
    next_cursor: str | None
    truncated_by_bytes: bool


class SnapshotRowsResponse(BaseModel):
    data: SnapshotRowsDataResponse
    meta: SnapshotRowsMetaResponse


class DatasetFreshnessResponse(BaseModel):
    dataset_id: UUID
    data_state: DatasetDataState
    snapshot_id: UUID | None
    snapshot_row_count: int | None
    last_success_at: datetime | None
    refresh_state: SnapshotRefreshState
    active_refresh_run_id: UUID | None
    refresh_requested_at: datetime | None
    refresh_started_at: datetime | None
    last_refresh_error: str | None
    last_refresh_error_at: datetime | None
    is_stale: bool


def _service(request: Request) -> DatasetSnapshotQueryService:
    return cast(DatasetSnapshotQueryService, request.app.state.snapshot_query_service)


_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ProblemDetails},
    401: {"model": ProblemDetails},
    403: {"model": ProblemDetails},
    404: {"model": ProblemDetails},
    409: {"model": ProblemDetails},
    413: {"model": ProblemDetails},
    422: {"model": ProblemDetails},
    503: {"model": ProblemDetails},
    504: {"model": ProblemDetails},
}


@router.get(
    "/{dataset_id}/freshness",
    response_model=DatasetFreshnessResponse,
    responses={
        401: {"model": ProblemDetails},
        403: {"model": ProblemDetails},
        404: {"model": ProblemDetails},
        503: {"model": ProblemDetails},
    },
)
async def read_dataset_freshness(
    dataset_id: UUID,
    request: Request,
    session: CurrentSession,
) -> DatasetFreshnessResponse:
    try:
        value = await _service(request).freshness(
            actor=session.user,
            dataset_id=dataset_id,
        )
    except DatasetSnapshotNotFoundError as exc:
        raise ApiProblem(
            status_code=404,
            code="dataset_not_found",
            title="Dataset not found",
            detail="The requested dataset does not exist.",
        ) from exc
    except DatasetSnapshotUnavailableError as exc:
        raise ApiProblem(
            status_code=503,
            code="snapshot_status_unavailable",
            title="Snapshot status unavailable",
            detail="The dataset snapshot status could not be read safely.",
        ) from exc
    return DatasetFreshnessResponse(
        dataset_id=value.dataset_id,
        data_state=value.data_state,
        snapshot_id=value.snapshot_id,
        snapshot_row_count=value.snapshot_row_count,
        last_success_at=value.last_success_at,
        refresh_state=value.refresh_state,
        active_refresh_run_id=value.active_refresh_run_id,
        refresh_requested_at=value.refresh_requested_at,
        refresh_started_at=value.refresh_started_at,
        last_refresh_error=value.last_refresh_error,
        last_refresh_error_at=value.last_refresh_error_at,
        is_stale=value.is_stale,
    )


@router.post(
    "/{dataset_id}/rows:query",
    response_model=SnapshotRowsResponse,
    responses=_RESPONSES,
)
async def query_dataset_rows(
    dataset_id: UUID,
    payload: SnapshotRowsQueryRequest,
    request: Request,
    session: CurrentSession,
) -> SnapshotRowsResponse:
    try:
        page = await _service(request).query(
            actor=session.user,
            dataset_id=dataset_id,
            value=SnapshotQuery(
                field_ids=tuple(payload.fields),
                filters=tuple(
                    SnapshotQueryFilter(
                        field_id=item.field_id,
                        operator=item.operator,
                        value=item.value,
                        values=tuple(item.values),
                    )
                    for item in payload.filters
                ),
                sorts=tuple(
                    SnapshotQuerySort(item.field_id, item.direction) for item in payload.sorts
                ),
                page_size=payload.page_size,
                cursor=payload.cursor,
            ),
            request_id=request_id(),
        )
    except DatasetSnapshotNotFoundError as exc:
        raise ApiProblem(
            status_code=404,
            code="dataset_not_found",
            title="Dataset not found",
            detail="The requested dataset does not exist.",
        ) from exc
    except DatasetSnapshotNotPublishedError as exc:
        raise ApiProblem(
            status_code=409,
            code="dataset_snapshot_not_published",
            title="Dataset snapshot not published",
            detail="Publish a successful dataset snapshot before querying rows.",
        ) from exc
    except InvalidSnapshotCursorError as exc:
        raise ApiProblem(
            status_code=400,
            code="invalid_snapshot_cursor",
            title="Invalid snapshot cursor",
            detail="The cursor does not match the active snapshot and query.",
        ) from exc
    except InvalidSnapshotQueryError as exc:
        raise ApiProblem(
            status_code=422,
            code="snapshot_query_invalid",
            title="Snapshot query invalid",
            detail=str(exc),
        ) from exc
    except DatasetSnapshotResultTooLargeError as exc:
        raise ApiProblem(
            status_code=413,
            code="snapshot_row_too_large",
            title="Snapshot row too large",
            detail="One result row exceeds the configured response-size limit.",
        ) from exc
    except DatasetSnapshotQueryTimeoutError as exc:
        raise ApiProblem(
            status_code=504,
            code="snapshot_query_timeout",
            title="Snapshot query timed out",
            detail="The snapshot query exceeded the configured execution time.",
        ) from exc
    except DatasetSnapshotUnavailableError as exc:
        raise ApiProblem(
            status_code=503,
            code="snapshot_query_unavailable",
            title="Snapshot query unavailable",
            detail="The active snapshot could not be queried safely.",
        ) from exc

    return SnapshotRowsResponse(
        data=SnapshotRowsDataResponse(
            columns=[
                SnapshotColumnResponse(
                    field_id=field.field_id,
                    name=field.name,
                    data_type=field.data_type,
                    nullable=field.nullable,
                )
                for field in page.columns
            ],
            rows=list(page.rows),
        ),
        meta=SnapshotRowsMetaResponse(
            snapshot_id=page.snapshot_id,
            snapshot_row_count=page.snapshot_row_count,
            last_success_at=page.snapshot_activated_at,
            refresh_state=page.refresh_state,
            last_refresh_error=page.last_refresh_error,
            last_refresh_error_at=page.last_refresh_error_at,
            is_stale=page.is_stale,
            next_cursor=page.next_cursor,
            truncated_by_bytes=page.truncated_by_bytes,
        ),
    )
