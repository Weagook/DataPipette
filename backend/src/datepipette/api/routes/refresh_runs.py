"""Owner-authorized endpoints for durable dataset refresh runs."""

from dataclasses import asdict
from datetime import datetime
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Header, Request, Response
from pydantic import BaseModel

from datepipette.api.dependencies.authentication import (
    CsrfSession,
    CurrentSession,
    request_id,
)
from datepipette.api.draft_revisions import draft_etag, expected_draft_revision
from datepipette.api.problems import ApiProblem, ProblemDetails
from datepipette.application.datasets import DatasetRevisionConflictError
from datepipette.application.refresh_runs import (
    RefreshDispatchUnavailableError,
    RefreshDraftNotValidatedError,
    RefreshRunNotFoundError,
    RefreshRunService,
    RefreshRunStateConflictError,
)
from datepipette.domain.refresh import RefreshRun, RefreshRunState, RefreshTriggerType

router = APIRouter(prefix="/datasets/{dataset_id}/refresh-runs", tags=["refresh-runs"])


class RefreshRunResponse(BaseModel):
    id: UUID
    dataset_id: UUID
    dataset_version_id: UUID
    requested_revision: int
    state: RefreshRunState
    requested_by: UUID
    trigger_type: RefreshTriggerType
    schedule_id: UUID | None
    dispatch_attempts: int
    dispatched_at: datetime | None
    attempt_count: int
    policy_revision: int | None
    error_code: str | None
    next_attempt_at: datetime | None
    cancel_requested_at: datetime | None
    cancelled_at: datetime | None
    snapshot_id: UUID | None
    requested_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime


def _service(request: Request) -> RefreshRunService:
    return cast(RefreshRunService, request.app.state.refresh_run_service)


def _response(run: RefreshRun) -> RefreshRunResponse:
    return RefreshRunResponse(**asdict(run))


_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ProblemDetails},
    401: {"model": ProblemDetails},
    403: {"model": ProblemDetails},
    404: {"model": ProblemDetails},
    409: {"model": ProblemDetails},
    422: {"model": ProblemDetails},
    428: {"model": ProblemDetails},
    503: {"model": ProblemDetails},
}


@router.post("", response_model=RefreshRunResponse, status_code=202, responses=_RESPONSES)
async def request_refresh_run(
    dataset_id: UUID,
    request: Request,
    response: Response,
    session: CsrfSession,
    if_match: Annotated[str | None, Header(alias="If-Match", max_length=64)] = None,
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ] = None,
) -> RefreshRunResponse:
    if idempotency_key is None:
        raise ApiProblem(
            status_code=428,
            code="idempotency_key_required",
            title="Idempotency key required",
            detail="Supply a unique Idempotency-Key for this refresh request.",
        )
    try:
        outcome = await _service(request).request(
            actor=session.user,
            dataset_id=dataset_id,
            expected_revision=expected_draft_revision(if_match),
            idempotency_key=idempotency_key,
            request_id=request_id(),
        )
    except RefreshRunNotFoundError as exc:
        raise ApiProblem(
            status_code=404,
            code="dataset_not_found",
            title="Dataset not found",
            detail="The requested dataset does not exist.",
        ) from exc
    except DatasetRevisionConflictError as exc:
        raise ApiProblem(
            status_code=409,
            code="dataset_revision_conflict",
            title="Dataset draft changed",
            detail="Refetch and validate the current draft before requesting a refresh.",
        ) from exc
    except RefreshDraftNotValidatedError as exc:
        raise ApiProblem(
            status_code=409,
            code="dataset_draft_not_validated",
            title="Dataset draft not validated",
            detail="Validate the current draft before requesting a refresh.",
        ) from exc
    except RefreshDispatchUnavailableError as exc:
        location = f"/api/v1/datasets/{dataset_id}/refresh-runs/{exc.run_id}"
        raise ApiProblem(
            status_code=503,
            code="refresh_dispatch_unavailable",
            title="Refresh dispatch unavailable",
            detail="The run was saved but Redis could not accept it. Retry with the same key.",
            headers={"Location": location, "Retry-After": "5"},
        ) from exc
    location = f"/api/v1/datasets/{dataset_id}/refresh-runs/{outcome.run.id}"
    response.headers["Location"] = location
    response.headers["ETag"] = draft_etag(outcome.run.requested_revision)
    response.headers["X-DatePipette-Coalesced"] = str(outcome.coalesced).lower()
    return _response(outcome.run)


@router.get("/{run_id}", response_model=RefreshRunResponse, responses=_RESPONSES)
async def read_refresh_run(
    dataset_id: UUID,
    run_id: UUID,
    request: Request,
    session: CurrentSession,
) -> RefreshRunResponse:
    try:
        run = await _service(request).get(
            actor=session.user,
            dataset_id=dataset_id,
            run_id=run_id,
        )
    except RefreshRunNotFoundError as exc:
        raise ApiProblem(
            status_code=404,
            code="refresh_run_not_found",
            title="Refresh run not found",
            detail="The requested refresh run does not exist for this dataset.",
        ) from exc
    return _response(run)


@router.post(
    "/{run_id}/cancel",
    response_model=RefreshRunResponse,
    responses=_RESPONSES,
)
async def cancel_refresh_run(
    dataset_id: UUID,
    run_id: UUID,
    request: Request,
    session: CsrfSession,
) -> RefreshRunResponse:
    try:
        run = await _service(request).cancel(
            actor=session.user,
            dataset_id=dataset_id,
            run_id=run_id,
            request_id=request_id(),
        )
    except RefreshRunNotFoundError as exc:
        raise ApiProblem(
            status_code=404,
            code="refresh_run_not_found",
            title="Refresh run not found",
            detail="The requested refresh run does not exist for this dataset.",
        ) from exc
    except RefreshRunStateConflictError as exc:
        raise ApiProblem(
            status_code=409,
            code="refresh_run_not_cancellable",
            title="Refresh run cannot be cancelled",
            detail="Only queued or running refresh runs can be cancelled.",
        ) from exc
    return _response(run)
