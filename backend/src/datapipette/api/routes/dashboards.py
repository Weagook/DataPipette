"""Private dashboard layout resources."""

import re
from datetime import date, datetime
from typing import Annotated, Any, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

from datapipette.api.chart_query_responses import (
    ChartQueryDataResponse,
    ChartQueryMetaResponse,
    chart_query_data_response,
    chart_query_meta_response,
)
from datapipette.api.dependencies.authentication import (
    CsrfSession,
    CurrentSession,
    DashboardReader,
    request_id,
)
from datapipette.api.problems import ApiProblem, ProblemDetails
from datapipette.application.charts import (
    ChartDatasetNotAvailableError,
    ChartNotFoundError,
    ChartQueryCapacityExceededError,
    ChartQueryResultTooLargeError,
    ChartQueryTimeoutError,
    ChartQueryUnavailableError,
    InvalidChartDefinitionError,
)
from datapipette.application.dashboards import (
    DashboardAlreadyExistsError,
    DashboardCreate,
    DashboardFilterNotFoundError,
    DashboardGrantNotFoundError,
    DashboardGrantPrincipalNotFoundError,
    DashboardItemNotFoundError,
    DashboardNotFoundError,
    DashboardRevisionConflictError,
    DashboardService,
    InvalidDashboardCursorError,
    InvalidDashboardDefinitionError,
    InvalidDashboardFilterSelectionError,
)
from datapipette.domain.access_control import GrantPrincipalType
from datapipette.domain.dashboards import (
    DASHBOARD_GRID_COLUMNS,
    MAX_DASHBOARD_FILTER_LABEL,
    MAX_DASHBOARD_FILTER_VALUES,
    MAX_DASHBOARD_FILTERS,
    MAX_DASHBOARD_ITEM_HEIGHT,
    MAX_DASHBOARD_ITEMS,
    MAX_DASHBOARD_ROW,
    Dashboard,
    DashboardDefinition,
    DashboardFilter,
    DashboardFilterKind,
    DashboardFilterSelection,
    DashboardFilterTarget,
    DashboardGrant,
    DashboardItem,
)

router = APIRouter(prefix="/dashboards", tags=["dashboards"])
_DASHBOARD_ETAG = re.compile(r'^"dashboard-([1-9][0-9]*)"$')


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DashboardItemPayload(StrictModel):
    id: UUID
    chart_id: UUID
    x: Annotated[StrictInt, Field(ge=0, lt=DASHBOARD_GRID_COLUMNS)]
    y: Annotated[StrictInt, Field(ge=0, lt=MAX_DASHBOARD_ROW)]
    width: Annotated[StrictInt, Field(ge=1, le=DASHBOARD_GRID_COLUMNS)]
    height: Annotated[StrictInt, Field(ge=1, le=MAX_DASHBOARD_ITEM_HEIGHT)]


class DashboardFilterTargetPayload(StrictModel):
    item_id: UUID
    field_id: UUID


class DashboardFilterPayload(StrictModel):
    id: UUID
    label: str = Field(min_length=1, max_length=MAX_DASHBOARD_FILTER_LABEL)
    kind: DashboardFilterKind
    targets: list[DashboardFilterTargetPayload] = Field(
        min_length=1,
        max_length=MAX_DASHBOARD_ITEMS,
    )


class DashboardDefinitionPayload(StrictModel):
    schema_version: Literal[1, 2] = 1
    items: list[DashboardItemPayload] = Field(
        default_factory=list,
        max_length=MAX_DASHBOARD_ITEMS,
    )
    filters: list[DashboardFilterPayload] = Field(
        default_factory=list,
        max_length=MAX_DASHBOARD_FILTERS,
        description="Viewer-controlled filters; requires schema version 2.",
    )


class DashboardFilterSelectionPayload(StrictModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    filter_id: UUID
    values: list[StrictStr | StrictInt | StrictBool] = Field(
        default_factory=list,
        max_length=MAX_DASHBOARD_FILTER_VALUES,
        description="Chosen values for a `values` filter.",
    )
    date_from: date | None = Field(
        default=None,
        alias="from",
        description="First included day for a `date_range` filter.",
    )
    date_to: date | None = Field(
        default=None,
        alias="to",
        description="Last included day for a `date_range` filter.",
    )


class DashboardItemQueryRequest(StrictModel):
    filters: list[DashboardFilterSelectionPayload] = Field(
        default_factory=list,
        max_length=MAX_DASHBOARD_FILTERS,
    )


class DashboardFilterValuesResponse(BaseModel):
    dashboard_id: UUID
    filter_id: UUID
    values: list[StrictStr | StrictInt | StrictBool]
    truncated: bool


class CreateDashboardRequest(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    definition: DashboardDefinitionPayload = Field(default_factory=DashboardDefinitionPayload)


class UpdateDashboardRequest(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    definition: DashboardDefinitionPayload


class DashboardResponse(BaseModel):
    id: UUID
    owner_id: UUID
    name: str
    revision: int
    definition: DashboardDefinitionPayload
    created_at: datetime
    updated_at: datetime


class CursorMeta(BaseModel):
    next_cursor: str | None


class DashboardCollectionResponse(BaseModel):
    data: list[DashboardResponse]
    meta: CursorMeta


class DashboardItemQueryMetaResponse(ChartQueryMetaResponse):
    dashboard_id: UUID
    dashboard_revision: int
    item_id: UUID


class DashboardItemQueryResponse(BaseModel):
    data: ChartQueryDataResponse
    meta: DashboardItemQueryMetaResponse


class DashboardGrantCreateRequest(StrictModel):
    principal_type: GrantPrincipalType
    principal_id: UUID


class DashboardGrantResponse(BaseModel):
    id: UUID
    dashboard_id: UUID
    principal_type: GrantPrincipalType
    principal_id: UUID
    created_by: UUID
    created_at: datetime


class DashboardGrantCollectionResponse(BaseModel):
    data: list[DashboardGrantResponse]


def _service(request: Request) -> DashboardService:
    return cast(DashboardService, request.app.state.dashboard_service)


def _definition(payload: DashboardDefinitionPayload) -> DashboardDefinition:
    try:
        return DashboardDefinition(
            schema_version=payload.schema_version,
            items=tuple(
                DashboardItem(
                    id=item.id,
                    chart_id=item.chart_id,
                    x=item.x,
                    y=item.y,
                    width=item.width,
                    height=item.height,
                )
                for item in payload.items
            ),
            filters=tuple(
                DashboardFilter(
                    id=item.id,
                    label=item.label,
                    kind=item.kind,
                    targets=tuple(
                        DashboardFilterTarget(item_id=target.item_id, field_id=target.field_id)
                        for target in item.targets
                    ),
                )
                for item in payload.filters
            ),
        )
    except ValueError as exc:
        raise InvalidDashboardDefinitionError(str(exc)) from exc


def _selections(
    payload: DashboardItemQueryRequest | None,
) -> tuple[DashboardFilterSelection, ...]:
    if payload is None:
        return ()
    try:
        return tuple(
            DashboardFilterSelection(
                filter_id=item.filter_id,
                values=tuple(item.values),
                date_from=item.date_from,
                date_to=item.date_to,
            )
            for item in payload.filters
        )
    except ValueError as exc:
        raise _invalid_selection(exc) from exc


def _invalid_selection(exc: Exception) -> ApiProblem:
    return ApiProblem(
        status_code=422,
        code="dashboard_filter_selection_invalid",
        title="Dashboard filter selection invalid",
        detail=str(exc) or "The dashboard filter selection is invalid.",
    )


def _response(value: Dashboard) -> DashboardResponse:
    return DashboardResponse(
        id=value.id,
        owner_id=value.owner_id,
        name=value.name,
        revision=value.revision,
        definition=DashboardDefinitionPayload.model_validate(value.definition.to_document()),
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


def _grant_response(value: DashboardGrant) -> DashboardGrantResponse:
    return DashboardGrantResponse(
        id=value.id,
        dashboard_id=value.dashboard_id,
        principal_type=value.principal_type,
        principal_id=value.principal_id,
        created_by=value.created_by,
        created_at=value.created_at,
    )


def _etag(revision: int) -> str:
    return f'"dashboard-{revision}"'


def _expected_revision(value: str | None) -> int:
    if value is None:
        raise ApiProblem(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            code="dashboard_revision_required",
            title="Dashboard revision required",
            detail="Supply the current dashboard ETag in the If-Match header.",
        )
    match = _DASHBOARD_ETAG.fullmatch(value)
    if match is None:
        raise ApiProblem(
            status_code=400,
            code="invalid_dashboard_revision",
            title="Invalid dashboard revision",
            detail="The If-Match header is not a valid dashboard ETag.",
        )
    return int(match.group(1))


def _not_found() -> ApiProblem:
    return ApiProblem(
        status_code=404,
        code="dashboard_not_found",
        title="Dashboard not found",
        detail="The requested dashboard does not exist.",
    )


def _invalid(exc: Exception) -> ApiProblem:
    return ApiProblem(
        status_code=422,
        code="dashboard_definition_invalid",
        title="Dashboard definition invalid",
        detail=str(exc) or "The dashboard layout is invalid.",
    )


_COMMON_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ProblemDetails},
    401: {"model": ProblemDetails},
    403: {"model": ProblemDetails},
    404: {"model": ProblemDetails},
    409: {"model": ProblemDetails},
    412: {"model": ProblemDetails},
    413: {"model": ProblemDetails},
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
    response_model=DashboardResponse,
    status_code=status.HTTP_201_CREATED,
    responses=_COMMON_RESPONSES,
)
async def create_dashboard(
    payload: CreateDashboardRequest,
    request: Request,
    response: Response,
    session: CsrfSession,
) -> DashboardResponse:
    try:
        dashboard = await _service(request).create(
            actor=session.user,
            value=DashboardCreate(payload.name, _definition(payload.definition)),
            request_id=request_id(),
        )
    except DashboardNotFoundError as exc:
        raise _not_found() from exc
    except DashboardAlreadyExistsError as exc:
        raise ApiProblem(
            status_code=409,
            code="dashboard_name_conflict",
            title="Dashboard name conflict",
            detail="The dashboard owner already has a dashboard with this name.",
        ) from exc
    except InvalidDashboardDefinitionError as exc:
        raise _invalid(exc) from exc
    response.headers["ETag"] = _etag(dashboard.revision)
    response.headers["Location"] = f"/api/v1/dashboards/{dashboard.id}"
    return _response(dashboard)


@router.get("", response_model=DashboardCollectionResponse, responses=_COMMON_RESPONSES)
async def list_dashboards(
    request: Request,
    session: DashboardReader,
    cursor: Annotated[str | None, Query(max_length=4096)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> DashboardCollectionResponse:
    try:
        page = await _service(request).list(actor=session.user, cursor=cursor, limit=limit)
    except InvalidDashboardCursorError as exc:
        raise ApiProblem(
            status_code=400,
            code="invalid_dashboard_cursor",
            title="Invalid dashboard cursor",
            detail="The dashboard collection cursor is invalid.",
        ) from exc
    return DashboardCollectionResponse(
        data=[_response(item) for item in page.dashboards],
        meta=CursorMeta(next_cursor=page.next_cursor),
    )


@router.get("/{dashboard_id}", response_model=DashboardResponse, responses=_COMMON_RESPONSES)
async def read_dashboard(
    dashboard_id: UUID,
    request: Request,
    response: Response,
    session: DashboardReader,
) -> DashboardResponse:
    try:
        dashboard = await _service(request).get(actor=session.user, dashboard_id=dashboard_id)
    except DashboardNotFoundError as exc:
        raise _not_found() from exc
    response.headers["ETag"] = _etag(dashboard.revision)
    return _response(dashboard)


@router.put("/{dashboard_id}", response_model=DashboardResponse, responses=_COMMON_RESPONSES)
async def update_dashboard(
    dashboard_id: UUID,
    payload: UpdateDashboardRequest,
    request: Request,
    response: Response,
    session: CsrfSession,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> DashboardResponse:
    try:
        dashboard = await _service(request).update(
            actor=session.user,
            dashboard_id=dashboard_id,
            expected_revision=_expected_revision(if_match),
            name=payload.name,
            definition=_definition(payload.definition),
            request_id=request_id(),
        )
    except DashboardNotFoundError as exc:
        raise _not_found() from exc
    except DashboardRevisionConflictError as exc:
        raise ApiProblem(
            status_code=412,
            code="dashboard_revision_conflict",
            title="Dashboard revision conflict",
            detail="Reload the dashboard before replacing a newer revision.",
        ) from exc
    except DashboardAlreadyExistsError as exc:
        raise ApiProblem(
            status_code=409,
            code="dashboard_name_conflict",
            title="Dashboard name conflict",
            detail="The dashboard owner already has a dashboard with this name.",
        ) from exc
    except InvalidDashboardDefinitionError as exc:
        raise _invalid(exc) from exc
    response.headers["ETag"] = _etag(dashboard.revision)
    return _response(dashboard)


@router.get(
    "/{dashboard_id}/grants",
    response_model=DashboardGrantCollectionResponse,
    responses=_COMMON_RESPONSES,
)
async def list_dashboard_grants(
    dashboard_id: UUID,
    request: Request,
    session: CurrentSession,
) -> DashboardGrantCollectionResponse:
    try:
        grants = await _service(request).list_grants(actor=session.user, dashboard_id=dashboard_id)
    except DashboardNotFoundError as exc:
        raise _not_found() from exc
    return DashboardGrantCollectionResponse(data=[_grant_response(value) for value in grants])


@router.post(
    "/{dashboard_id}/grants",
    response_model=DashboardGrantResponse,
    status_code=status.HTTP_201_CREATED,
    responses=_COMMON_RESPONSES,
)
async def create_dashboard_grant(
    dashboard_id: UUID,
    payload: DashboardGrantCreateRequest,
    request: Request,
    session: CsrfSession,
) -> DashboardGrantResponse:
    try:
        grant = await _service(request).create_grant(
            actor=session.user,
            dashboard_id=dashboard_id,
            principal_type=payload.principal_type,
            principal_id=payload.principal_id,
            request_id=request_id(),
        )
    except DashboardNotFoundError as exc:
        raise _not_found() from exc
    except DashboardGrantPrincipalNotFoundError as exc:
        raise ApiProblem(
            status_code=404,
            code="dashboard_grant_principal_not_found",
            title="Dashboard grant principal not found",
            detail="The active user or group does not exist.",
        ) from exc
    return _grant_response(grant)


@router.delete(
    "/{dashboard_id}/grants/{grant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_COMMON_RESPONSES,
)
async def revoke_dashboard_grant(
    dashboard_id: UUID,
    grant_id: UUID,
    request: Request,
    session: CsrfSession,
) -> Response:
    try:
        await _service(request).revoke_grant(
            actor=session.user,
            dashboard_id=dashboard_id,
            grant_id=grant_id,
            request_id=request_id(),
        )
    except DashboardNotFoundError as exc:
        raise _not_found() from exc
    except DashboardGrantNotFoundError as exc:
        raise ApiProblem(
            status_code=404,
            code="dashboard_grant_not_found",
            title="Dashboard grant not found",
            detail="The requested dashboard grant does not exist.",
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{dashboard_id}/items/{item_id}/data:query",
    response_model=DashboardItemQueryResponse,
    responses=_QUERY_RESPONSES,
)
async def query_dashboard_item_data(
    dashboard_id: UUID,
    item_id: UUID,
    request: Request,
    session: DashboardReader,
    payload: DashboardItemQueryRequest | None = None,
) -> DashboardItemQueryResponse:
    try:
        result = await _service(request).query_item(
            actor=session.user,
            dashboard_id=dashboard_id,
            item_id=item_id,
            request_id=request_id(),
            selections=_selections(payload),
        )
    except DashboardNotFoundError as exc:
        raise _not_found() from exc
    except InvalidDashboardFilterSelectionError as exc:
        raise _invalid_selection(exc) from exc
    except DashboardItemNotFoundError as exc:
        raise ApiProblem(
            status_code=404,
            code="dashboard_item_not_found",
            title="Dashboard item not found",
            detail="The requested item is not present in the dashboard.",
        ) from exc
    except (ChartNotFoundError, ChartDatasetNotAvailableError) as exc:
        raise ApiProblem(
            status_code=404,
            code="dashboard_item_unavailable",
            title="Dashboard item unavailable",
            detail="The saved chart or its published dataset is not available.",
        ) from exc
    except InvalidChartDefinitionError as exc:
        raise ApiProblem(
            status_code=409,
            code="dashboard_item_invalid",
            title="Dashboard item invalid",
            detail="The saved chart no longer matches the active published dataset.",
        ) from exc
    except ChartQueryResultTooLargeError as exc:
        raise ApiProblem(
            status_code=413,
            code="dashboard_item_result_row_too_large",
            title="Dashboard item result row too large",
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
            code="dashboard_item_query_timeout",
            title="Dashboard item query timed out",
            detail="The chart query exceeded the configured execution time.",
        ) from exc
    except ChartQueryUnavailableError as exc:
        raise ApiProblem(
            status_code=503,
            code="dashboard_item_query_unavailable",
            title="Dashboard item query unavailable",
            detail="The active snapshot could not be queried safely.",
        ) from exc
    chart_meta = chart_query_meta_response(result.chart_result)
    return DashboardItemQueryResponse(
        data=chart_query_data_response(result.chart_result),
        meta=DashboardItemQueryMetaResponse(
            dashboard_id=result.dashboard_id,
            dashboard_revision=result.dashboard_revision,
            item_id=result.item_id,
            **chart_meta.model_dump(),
        ),
    )


@router.get(
    "/{dashboard_id}/filters/{filter_id}/values",
    response_model=DashboardFilterValuesResponse,
    responses=_QUERY_RESPONSES,
)
async def list_dashboard_filter_values(
    dashboard_id: UUID,
    filter_id: UUID,
    request: Request,
    session: DashboardReader,
) -> DashboardFilterValuesResponse:
    """List the choices for a `values` filter from the active snapshots of its charts."""

    try:
        result = await _service(request).list_filter_values(
            actor=session.user,
            dashboard_id=dashboard_id,
            filter_id=filter_id,
        )
    except DashboardNotFoundError as exc:
        raise _not_found() from exc
    except DashboardFilterNotFoundError as exc:
        raise ApiProblem(
            status_code=404,
            code="dashboard_filter_not_found",
            title="Dashboard filter not found",
            detail="The dashboard has no value filter with this identifier.",
        ) from exc
    except InvalidChartDefinitionError as exc:
        raise ApiProblem(
            status_code=409,
            code="dashboard_filter_invalid",
            title="Dashboard filter invalid",
            detail="A filter field no longer matches the active published dataset.",
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
            code="dashboard_filter_values_timeout",
            title="Dashboard filter values timed out",
            detail="Listing filter values exceeded the configured execution time.",
        ) from exc
    except (ChartQueryUnavailableError, ChartQueryResultTooLargeError) as exc:
        raise ApiProblem(
            status_code=503,
            code="dashboard_filter_values_unavailable",
            title="Dashboard filter values unavailable",
            detail="The active snapshots could not be queried safely.",
        ) from exc
    return DashboardFilterValuesResponse(
        dashboard_id=result.dashboard_id,
        filter_id=result.filter_id,
        values=list(result.values),
        truncated=result.truncated,
    )
