"""Administrator-managed Query Safety policy endpoints."""

import re
from dataclasses import asdict
from datetime import datetime
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Header, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from datepipette.api.dependencies.authentication import (
    AdministratorCsrfSession,
    AdministratorSession,
    request_id,
)
from datepipette.api.problems import ApiProblem, ProblemDetails
from datepipette.application.query_safety import (
    ManagedQuerySafetyPolicy,
    QuerySafetyPolicyRevisionConflictError,
    QuerySafetyPolicyService,
)
from datepipette.domain.datasets import QuerySafetyPolicy

router = APIRouter(prefix="/query-safety-policy", tags=["query safety"])
_ETAG = re.compile(r'^"query-safety-policy-(0|[1-9][0-9]*)"$')


class ReplaceQuerySafetyPolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    estimated_rows_warning: int = Field(ge=1, le=1_000_000_000_000)
    estimated_rows_block: int = Field(ge=2, le=1_000_000_000_000)
    total_cost_warning: float = Field(gt=0, le=1_000_000_000_000)
    total_cost_block: float = Field(gt=0, le=1_000_000_000_000)
    join_count_warning: int = Field(ge=1, le=63)
    join_count_block: int = Field(ge=2, le=64)
    sequential_scan_rows_warning: int = Field(ge=1, le=1_000_000_000_000)
    sort_rows_warning: int = Field(ge=1, le=1_000_000_000_000)
    stale_statistics_days: int = Field(ge=1, le=3650)

    @model_validator(mode="after")
    def validate_threshold_order(self) -> "ReplaceQuerySafetyPolicyRequest":
        if self.estimated_rows_warning >= self.estimated_rows_block:
            raise ValueError("Estimated row warning threshold must be below block threshold")
        if self.total_cost_warning >= self.total_cost_block:
            raise ValueError("Planner cost warning threshold must be below block threshold")
        if self.join_count_warning >= self.join_count_block:
            raise ValueError("Join warning threshold must be below block threshold")
        return self


class QuerySafetyPolicyResponse(BaseModel):
    revision: int
    estimated_rows_warning: int
    estimated_rows_block: int
    total_cost_warning: float
    total_cost_block: float
    join_count_warning: int
    join_count_block: int
    sequential_scan_rows_warning: int
    sort_rows_warning: int
    stale_statistics_days: int
    updated_at: datetime | None
    updated_by: UUID | None


def _service(request: Request) -> QuerySafetyPolicyService:
    return cast(QuerySafetyPolicyService, request.app.state.query_safety_policy_service)


def _etag(revision: int) -> str:
    return f'"query-safety-policy-{revision}"'


def _expected_revision(value: str | None) -> int:
    if value is None:
        raise ApiProblem(
            status_code=428,
            code="query_safety_policy_revision_required",
            title="Query Safety policy revision required",
            detail="Supply the current policy ETag in the If-Match header.",
        )
    match = _ETAG.fullmatch(value)
    if match is None:
        raise ApiProblem(
            status_code=400,
            code="invalid_query_safety_policy_revision",
            title="Invalid Query Safety policy revision",
            detail="The If-Match header is not a valid Query Safety policy ETag.",
        )
    return int(match.group(1))


def _response(value: ManagedQuerySafetyPolicy) -> QuerySafetyPolicyResponse:
    return QuerySafetyPolicyResponse(
        revision=value.revision,
        **asdict(value.policy),
        updated_at=value.updated_at,
        updated_by=value.updated_by,
    )


_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ProblemDetails},
    401: {"model": ProblemDetails},
    403: {"model": ProblemDetails},
    409: {"model": ProblemDetails},
    422: {"model": ProblemDetails},
    428: {"model": ProblemDetails},
}
_ETAG_HEADER = {
    "ETag": {
        "description": "Opaque revision of the Query Safety policy.",
        "schema": {"type": "string", "example": '"query-safety-policy-2"'},
    }
}


@router.get(
    "",
    response_model=QuerySafetyPolicyResponse,
    responses={**_RESPONSES, 200: {"headers": _ETAG_HEADER}},
)
async def read_query_safety_policy(
    request: Request,
    response: Response,
    session: AdministratorSession,
) -> QuerySafetyPolicyResponse:
    value = await _service(request).get(actor=session.user)
    response.headers["ETag"] = _etag(value.revision)
    return _response(value)


@router.put(
    "",
    response_model=QuerySafetyPolicyResponse,
    responses={**_RESPONSES, 200: {"headers": _ETAG_HEADER}},
)
async def replace_query_safety_policy(
    payload: ReplaceQuerySafetyPolicyRequest,
    request: Request,
    response: Response,
    session: AdministratorCsrfSession,
    if_match: Annotated[str | None, Header(alias="If-Match", max_length=128)] = None,
) -> QuerySafetyPolicyResponse:
    try:
        value = await _service(request).replace(
            actor=session.user,
            policy=QuerySafetyPolicy(**payload.model_dump()),
            expected_revision=_expected_revision(if_match),
            request_id=request_id(),
        )
    except QuerySafetyPolicyRevisionConflictError as exc:
        raise ApiProblem(
            status_code=409,
            code="query_safety_policy_revision_conflict",
            title="Query Safety policy changed",
            detail="Refetch the current policy before replacing it.",
        ) from exc
    response.headers["ETag"] = _etag(value.revision)
    return _response(value)
