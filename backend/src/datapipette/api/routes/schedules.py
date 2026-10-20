"""Owner-authorized dataset refresh schedule endpoints."""

import re
from datetime import datetime, time
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Header, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from datapipette.api.dependencies.authentication import CsrfSession, CurrentSession, request_id
from datapipette.api.problems import ApiProblem, ProblemDetails
from datapipette.application.schedules import (
    RefreshScheduleNotFoundError,
    RefreshScheduleRequiresPublishedDatasetError,
    RefreshScheduleRevisionConflictError,
    RefreshScheduleRevisionRequiredError,
    RefreshScheduleService,
)
from datapipette.domain.schedules import (
    DatasetRefreshSchedule,
    RefreshScheduleKind,
    RefreshScheduleSpec,
)

router = APIRouter(prefix="/datasets/{dataset_id}/refresh-schedule", tags=["refresh-schedules"])
_ETAG = re.compile(r'^"schedule-([1-9][0-9]*)"$')


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReplaceRefreshScheduleRequest(StrictModel):
    kind: RefreshScheduleKind
    interval_minutes: int | None = Field(default=None, ge=5, le=10_080)
    daily_time: time | None = None
    enabled: bool


class RefreshScheduleResponse(BaseModel):
    id: UUID
    dataset_id: UUID
    kind: RefreshScheduleKind
    interval_minutes: int | None
    daily_time: time | None
    enabled: bool
    timezone: str
    next_run_at: datetime | None
    last_triggered_at: datetime | None
    revision: int
    created_by: UUID
    updated_by: UUID
    created_at: datetime
    updated_at: datetime


def _service(request: Request) -> RefreshScheduleService:
    return cast(RefreshScheduleService, request.app.state.refresh_schedule_service)


def _response(value: DatasetRefreshSchedule) -> RefreshScheduleResponse:
    return RefreshScheduleResponse(
        id=value.id,
        dataset_id=value.dataset_id,
        kind=value.spec.kind,
        interval_minutes=value.spec.interval_minutes,
        daily_time=value.spec.daily_time,
        enabled=value.enabled,
        timezone=value.timezone,
        next_run_at=value.next_run_at,
        last_triggered_at=value.last_triggered_at,
        revision=value.revision,
        created_by=value.created_by,
        updated_by=value.updated_by,
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


def _etag(revision: int) -> str:
    return f'"schedule-{revision}"'


def _expected_revision(value: str | None) -> int | None:
    if value is None:
        return None
    match = _ETAG.fullmatch(value.strip())
    if match is None:
        raise ApiProblem(
            status_code=400,
            code="invalid_schedule_revision",
            title="Invalid schedule revision",
            detail='If-Match must contain an ETag such as "schedule-2".',
        )
    return int(match.group(1))


_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ProblemDetails},
    401: {"model": ProblemDetails},
    403: {"model": ProblemDetails},
    404: {"model": ProblemDetails},
    409: {"model": ProblemDetails},
    422: {"model": ProblemDetails},
    428: {"model": ProblemDetails},
}
_ETAG_HEADER = {
    "ETag": {
        "description": "Opaque revision of the refresh schedule.",
        "schema": {"type": "string", "example": '"schedule-2"'},
    }
}


@router.get(
    "",
    response_model=RefreshScheduleResponse,
    responses={**_RESPONSES, 200: {"headers": _ETAG_HEADER}},
)
async def read_refresh_schedule(
    dataset_id: UUID,
    request: Request,
    response: Response,
    session: CurrentSession,
) -> RefreshScheduleResponse:
    try:
        value = await _service(request).get(actor=session.user, dataset_id=dataset_id)
    except RefreshScheduleNotFoundError as exc:
        raise ApiProblem(
            status_code=404,
            code="refresh_schedule_not_found",
            title="Refresh schedule not found",
            detail="The dataset or its refresh schedule does not exist.",
        ) from exc
    response.headers["ETag"] = _etag(value.revision)
    return _response(value)


@router.put(
    "",
    response_model=RefreshScheduleResponse,
    responses={**_RESPONSES, 200: {"headers": _ETAG_HEADER}},
)
async def replace_refresh_schedule(
    dataset_id: UUID,
    payload: ReplaceRefreshScheduleRequest,
    request: Request,
    response: Response,
    session: CsrfSession,
    if_match: Annotated[str | None, Header(alias="If-Match", max_length=64)] = None,
) -> RefreshScheduleResponse:
    try:
        value = await _service(request).replace(
            actor=session.user,
            dataset_id=dataset_id,
            spec=RefreshScheduleSpec(
                kind=payload.kind,
                interval_minutes=payload.interval_minutes,
                daily_time=payload.daily_time,
            ),
            enabled=payload.enabled,
            expected_revision=_expected_revision(if_match),
            request_id=request_id(),
        )
    except RefreshScheduleNotFoundError as exc:
        raise ApiProblem(
            status_code=404,
            code="dataset_not_found",
            title="Dataset not found",
            detail="The requested dataset does not exist.",
        ) from exc
    except RefreshScheduleRevisionRequiredError as exc:
        raise ApiProblem(
            status_code=428,
            code="schedule_revision_required",
            title="Schedule revision required",
            detail="Supply the current schedule ETag in If-Match.",
        ) from exc
    except RefreshScheduleRevisionConflictError as exc:
        raise ApiProblem(
            status_code=409,
            code="schedule_revision_conflict",
            title="Refresh schedule changed",
            detail="Refetch the schedule before replacing it.",
        ) from exc
    except RefreshScheduleRequiresPublishedDatasetError as exc:
        raise ApiProblem(
            status_code=409,
            code="dataset_not_published",
            title="Dataset not published",
            detail="Publish the dataset before enabling its refresh schedule.",
        ) from exc
    except ValueError as exc:
        raise ApiProblem(
            status_code=422,
            code="refresh_schedule_invalid",
            title="Refresh schedule invalid",
            detail="The refresh cadence is inconsistent.",
        ) from exc
    response.headers["ETag"] = _etag(value.revision)
    return _response(value)
