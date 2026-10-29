"""Private chart specifications and bounded aggregate data queries."""

import re
from datetime import datetime
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from datapipette.api.chart_query_responses import (
    ChartPreviewResponse,
    ChartQueryResponse,
    chart_preview_response,
    chart_query_response,
)
from datapipette.api.dependencies.authentication import CsrfSession, CurrentSession, request_id
from datapipette.api.problems import ApiProblem, ProblemDetails
from datapipette.application.charts import (
    ChartAlreadyExistsError,
    ChartCreate,
    ChartDatasetNotAvailableError,
    ChartNotFoundError,
    ChartQueryCapacityExceededError,
    ChartQueryResultTooLargeError,
    ChartQueryTimeoutError,
    ChartQueryUnavailableError,
    ChartRevisionConflictError,
    ChartService,
    InvalidChartCursorError,
    InvalidChartDefinitionError,
)
from datapipette.domain.charts import (
    MAX_CHART_FILTERS,
    MAX_CHART_MEASURES,
    MAX_CHART_RESULT_ROWS,
    MAX_CHART_SORTS,
    Chart,
    ChartAggregation,
    ChartDefinition,
    ChartDimension,
    ChartMeasure,
    ChartSort,
    ChartSortTargetType,
    ChartTimeBucket,
    ChartType,
)
from datapipette.domain.snapshot_queries import (
    SnapshotFilterOperator,
    SnapshotQueryFilter,
    SnapshotSortDirection,
)

router = APIRouter(prefix="/charts", tags=["charts"])
_CHART_ETAG = re.compile(r'^"chart-([1-9][0-9]*)"$')


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChartDimensionPayload(StrictModel):
    field_id: UUID
    time_bucket: ChartTimeBucket | None = None


class ChartMeasurePayload(StrictModel):
    id: UUID
    field_id: UUID
    aggregation: ChartAggregation
    label: str = Field(min_length=1, max_length=100)

    @field_validator("label")
    @classmethod
    def normalize_label(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("Value must not be empty")
        return normalized


class ChartFilterPayload(StrictModel):
    field_id: UUID
    operator: SnapshotFilterOperator
    value: Any = None
    values: list[Any] = Field(default_factory=list, max_length=100)


class ChartSortPayload(StrictModel):
    target_type: ChartSortTargetType
    target_id: UUID
    direction: SnapshotSortDirection = SnapshotSortDirection.ASCENDING


class ChartDefinitionPayload(StrictModel):
    schema_version: int = 2
    chart_type: ChartType
    dimensions: list[ChartDimensionPayload] = Field(default_factory=list, max_length=1)
    measures: list[ChartMeasurePayload] = Field(
        min_length=1,
        max_length=MAX_CHART_MEASURES,
    )
    filters: list[ChartFilterPayload] = Field(
        default_factory=list,
        max_length=MAX_CHART_FILTERS,
    )
    sorts: list[ChartSortPayload] = Field(
        default_factory=list,
        max_length=MAX_CHART_SORTS,
    )
    limit: int = Field(default=100, ge=1, le=MAX_CHART_RESULT_ROWS)


class CreateChartRequest(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    dataset_id: UUID
    definition: ChartDefinitionPayload


class PreviewChartRequest(StrictModel):
    dataset_id: UUID
    definition: ChartDefinitionPayload


class UpdateChartRequest(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    definition: ChartDefinitionPayload


class ChartResponse(BaseModel):
    id: UUID
    dataset_id: UUID
    owner_id: UUID
    name: str
    revision: int
    definition: ChartDefinitionPayload
    created_at: datetime
    updated_at: datetime


class CursorMeta(BaseModel):
    next_cursor: str | None


class ChartCollectionResponse(BaseModel):
    data: list[ChartResponse]
    meta: CursorMeta


def _service(request: Request) -> ChartService:
    return cast(ChartService, request.app.state.chart_service)


def _definition(payload: ChartDefinitionPayload) -> ChartDefinition:
    try:
        return ChartDefinition(
            schema_version=payload.schema_version,
            chart_type=payload.chart_type,
            dimensions=tuple(
                ChartDimension(item.field_id, item.time_bucket) for item in payload.dimensions
            ),
            measures=tuple(
                ChartMeasure(item.id, item.field_id, item.aggregation, item.label)
                for item in payload.measures
            ),
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
                ChartSort(item.target_type, item.target_id, item.direction)
                for item in payload.sorts
            ),
            limit=payload.limit,
        )
    except ValueError as exc:
        raise InvalidChartDefinitionError(str(exc)) from exc


def _chart_response(value: Chart) -> ChartResponse:
    return ChartResponse(
        id=value.id,
        dataset_id=value.dataset_id,
        owner_id=value.owner_id,
        name=value.name,
        revision=value.revision,
        definition=ChartDefinitionPayload.model_validate(value.definition.to_document()),
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


def _etag(revision: int) -> str:
    return f'"chart-{revision}"'


def _expected_revision(value: str | None) -> int:
    if value is None:
        raise ApiProblem(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            code="chart_revision_required",
            title="Chart revision required",
            detail="Supply the current chart ETag in the If-Match header.",
        )
    match = _CHART_ETAG.fullmatch(value)
    if match is None:
        raise ApiProblem(
            status_code=400,
            code="invalid_chart_revision",
            title="Invalid chart revision",
            detail="The If-Match header is not a valid chart ETag.",
        )
    return int(match.group(1))


def _not_found() -> ApiProblem:
    return ApiProblem(
        status_code=404,
        code="chart_not_found",
        title="Chart not found",
        detail="The requested chart does not exist.",
    )


def _invalid(exc: Exception) -> ApiProblem:
    return ApiProblem(
        status_code=422,
        code="chart_definition_invalid",
        title="Chart definition invalid",
        detail=str(exc) or "The chart definition is inconsistent with the published dataset.",
    )


_COMMON_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ProblemDetails},
    401: {"model": ProblemDetails},
    403: {"model": ProblemDetails},
    404: {"model": ProblemDetails},
    409: {"model": ProblemDetails},
    412: {"model": ProblemDetails},
    422: {"model": ProblemDetails},
    428: {"model": ProblemDetails},
    503: {"model": ProblemDetails},
    504: {"model": ProblemDetails},
}
_QUERY_RESPONSES = {
    **_COMMON_RESPONSES,
    503: {
        "model": ProblemDetails,
        "headers": {
            "Retry-After": {
                "description": "Seconds to wait after interactive capacity exhaustion.",
                "schema": {"type": "integer", "minimum": 1},
            }
        },
    },
}


@router.post(
    "",
    response_model=ChartResponse,
    status_code=status.HTTP_201_CREATED,
    responses=_COMMON_RESPONSES,
)
async def create_chart(
    payload: CreateChartRequest,
    request: Request,
    response: Response,
    session: CsrfSession,
) -> ChartResponse:
    try:
        chart = await _service(request).create(
            actor=session.user,
            value=ChartCreate(payload.name, payload.dataset_id, _definition(payload.definition)),
            request_id=request_id(),
        )
    except ChartDatasetNotAvailableError as exc:
        raise ApiProblem(
            status_code=404,
            code="dataset_not_found",
            title="Dataset not found",
            detail="The requested published dataset is not available.",
        ) from exc
    except ChartAlreadyExistsError as exc:
        raise ApiProblem(
            status_code=409,
            code="chart_name_conflict",
            title="Chart name conflict",
            detail="The chart owner already has a chart with this name.",
        ) from exc
    except InvalidChartDefinitionError as exc:
        raise _invalid(exc) from exc
    response.headers["ETag"] = _etag(chart.revision)
    response.headers["Location"] = f"/api/v1/charts/{chart.id}"
    return _chart_response(chart)


@router.get("", response_model=ChartCollectionResponse, responses=_COMMON_RESPONSES)
async def list_charts(
    request: Request,
    session: CurrentSession,
    cursor: Annotated[str | None, Query(max_length=4096)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ChartCollectionResponse:
    try:
        page = await _service(request).list(actor=session.user, cursor=cursor, limit=limit)
    except InvalidChartCursorError as exc:
        raise ApiProblem(
            status_code=400,
            code="invalid_chart_cursor",
            title="Invalid chart cursor",
            detail="The chart collection cursor is invalid.",
        ) from exc
    return ChartCollectionResponse(
        data=[_chart_response(item) for item in page.charts],
        meta=CursorMeta(next_cursor=page.next_cursor),
    )


@router.get("/{chart_id}", response_model=ChartResponse, responses=_COMMON_RESPONSES)
async def read_chart(
    chart_id: UUID,
    request: Request,
    response: Response,
    session: CurrentSession,
) -> ChartResponse:
    try:
        chart = await _service(request).get(actor=session.user, chart_id=chart_id)
    except ChartNotFoundError as exc:
        raise _not_found() from exc
    response.headers["ETag"] = _etag(chart.revision)
    return _chart_response(chart)


@router.put("/{chart_id}", response_model=ChartResponse, responses=_COMMON_RESPONSES)
async def update_chart(
    chart_id: UUID,
    payload: UpdateChartRequest,
    request: Request,
    response: Response,
    session: CsrfSession,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> ChartResponse:
    try:
        chart = await _service(request).update(
            actor=session.user,
            chart_id=chart_id,
            expected_revision=_expected_revision(if_match),
            name=payload.name,
            definition=_definition(payload.definition),
            request_id=request_id(),
        )
    except ChartNotFoundError as exc:
        raise _not_found() from exc
    except ChartDatasetNotAvailableError as exc:
        raise ApiProblem(
            status_code=404,
            code="dataset_not_found",
            title="Dataset not found",
            detail="The chart's published dataset is not available.",
        ) from exc
    except ChartRevisionConflictError as exc:
        raise ApiProblem(
            status_code=412,
            code="chart_revision_conflict",
            title="Chart revision conflict",
            detail="Reload the chart before replacing a newer revision.",
        ) from exc
    except ChartAlreadyExistsError as exc:
        raise ApiProblem(
            status_code=409,
            code="chart_name_conflict",
            title="Chart name conflict",
            detail="The chart owner already has a chart with this name.",
        ) from exc
    except InvalidChartDefinitionError as exc:
        raise _invalid(exc) from exc
    response.headers["ETag"] = _etag(chart.revision)
    return _chart_response(chart)


@router.post(
    "/data:preview",
    response_model=ChartPreviewResponse,
    responses=_QUERY_RESPONSES,
)
async def preview_chart_data(
    payload: PreviewChartRequest,
    request: Request,
    session: CurrentSession,
) -> ChartPreviewResponse:
    """Run an unsaved chart definition against the dataset's active snapshot."""
    try:
        result = await _service(request).preview(
            actor=session.user,
            dataset_id=payload.dataset_id,
            definition=_definition(payload.definition),
            request_id=request_id(),
        )
    except ChartDatasetNotAvailableError as exc:
        raise ApiProblem(
            status_code=404,
            code="dataset_not_found",
            title="Dataset not found",
            detail="The requested published dataset is not available.",
        ) from exc
    except InvalidChartDefinitionError as exc:
        raise _invalid(exc) from exc
    except ChartQueryResultTooLargeError as exc:
        raise ApiProblem(
            status_code=413,
            code="chart_result_row_too_large",
            title="Chart result row too large",
            detail="One chart result row exceeds the response-size limit.",
        ) from exc
    except ChartQueryCapacityExceededError as exc:
        raise ApiProblem(
            status_code=503,
            code="interactive_query_capacity_exceeded",
            title="Interactive query capacity exceeded",
            detail="All interactive query slots are busy. Retry shortly.",
            headers={"Retry-After": "1"},
        ) from exc
    except ChartQueryTimeoutError as exc:
        raise ApiProblem(
            status_code=504,
            code="chart_query_timeout",
            title="Chart query timed out",
            detail="The chart query exceeded the configured execution time.",
        ) from exc
    except ChartQueryUnavailableError as exc:
        raise ApiProblem(
            status_code=503,
            code="chart_query_unavailable",
            title="Chart query unavailable",
            detail="The active snapshot could not be queried safely.",
        ) from exc
    return chart_preview_response(result)


@router.post(
    "/{chart_id}/data:query",
    response_model=ChartQueryResponse,
    responses=_QUERY_RESPONSES,
)
async def query_chart_data(
    chart_id: UUID,
    request: Request,
    session: CurrentSession,
) -> ChartQueryResponse:
    try:
        result = await _service(request).query(
            actor=session.user,
            chart_id=chart_id,
            request_id=request_id(),
        )
    except (ChartNotFoundError, ChartDatasetNotAvailableError) as exc:
        raise _not_found() from exc
    except InvalidChartDefinitionError as exc:
        raise ApiProblem(
            status_code=409,
            code="chart_invalid",
            title="Chart invalid",
            detail="The saved chart no longer matches the active published dataset.",
        ) from exc
    except ChartQueryResultTooLargeError as exc:
        raise ApiProblem(
            status_code=413,
            code="chart_result_row_too_large",
            title="Chart result row too large",
            detail="One chart result row exceeds the response-size limit.",
        ) from exc
    except ChartQueryCapacityExceededError as exc:
        raise ApiProblem(
            status_code=503,
            code="interactive_query_capacity_exceeded",
            title="Interactive query capacity exceeded",
            detail="All interactive query slots are busy. Retry shortly.",
            headers={"Retry-After": "1"},
        ) from exc
    except ChartQueryTimeoutError as exc:
        raise ApiProblem(
            status_code=504,
            code="chart_query_timeout",
            title="Chart query timed out",
            detail="The chart query exceeded the configured execution time.",
        ) from exc
    except ChartQueryUnavailableError as exc:
        raise ApiProblem(
            status_code=503,
            code="chart_query_unavailable",
            title="Chart query unavailable",
            detail="The active snapshot could not be queried safely.",
        ) from exc
    return chart_query_response(result)
