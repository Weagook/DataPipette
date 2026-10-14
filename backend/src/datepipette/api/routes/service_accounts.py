"""Administrator-only service-account and API-token management."""

from datetime import datetime
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from datepipette.api.dependencies.authentication import (
    AdministratorCsrfSession,
    AdministratorSession,
    request_id,
)
from datepipette.api.problems import ApiProblem, ProblemDetails
from datepipette.application.service_accounts import (
    ApiTokenNotFoundError,
    ServiceAccountAlreadyExistsError,
    ServiceAccountNotFoundError,
    ServiceAccountService,
    ServiceAccountUpdate,
)
from datepipette.domain.identity import AccountStatus, SystemRole
from datepipette.domain.service_accounts import ApiToken, ApiTokenScope, ServiceAccount

router = APIRouter(prefix="/service-accounts", tags=["service accounts"])


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateServiceAccountRequest(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    role: SystemRole


class UpdateServiceAccountRequest(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    role: SystemRole | None = None
    status: AccountStatus | None = None

    @model_validator(mode="after")
    def require_change(self) -> "UpdateServiceAccountRequest":
        if not self.model_fields_set or all(
            getattr(self, field_name) is None for field_name in self.model_fields_set
        ):
            raise ValueError("At least one non-null field must be provided")
        return self


class ServiceAccountResponse(BaseModel):
    id: UUID
    name: str
    role: SystemRole
    status: AccountStatus
    created_at: datetime
    updated_at: datetime


class ServiceAccountCollectionResponse(BaseModel):
    data: list[ServiceAccountResponse]


class CreateApiTokenRequest(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    scopes: set[ApiTokenScope] = Field(min_length=1)
    expires_at: datetime | None = None


class ApiTokenResponse(BaseModel):
    id: UUID
    service_account_id: UUID
    name: str
    scopes: set[ApiTokenScope]
    expires_at: datetime | None
    revoked_at: datetime | None
    last_used_at: datetime | None
    created_by: UUID
    created_at: datetime


class IssuedApiTokenResponse(ApiTokenResponse):
    token: str


class ApiTokenCollectionResponse(BaseModel):
    data: list[ApiTokenResponse]


_RESPONSES: dict[int | str, dict[str, object]] = {
    400: {"model": ProblemDetails},
    401: {"model": ProblemDetails},
    403: {"model": ProblemDetails},
    404: {"model": ProblemDetails},
    409: {"model": ProblemDetails},
    422: {"model": ProblemDetails},
}


def _service(request: Request) -> ServiceAccountService:
    return cast(ServiceAccountService, request.app.state.service_account_service)


def _account_response(value: ServiceAccount) -> ServiceAccountResponse:
    return ServiceAccountResponse(
        id=value.id,
        name=value.name,
        role=value.role,
        status=value.status,
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


def _token_response(value: ApiToken) -> ApiTokenResponse:
    return ApiTokenResponse(
        id=value.id,
        service_account_id=value.service_account_id,
        name=value.name,
        scopes=set(value.scopes),
        expires_at=value.expires_at,
        revoked_at=value.revoked_at,
        last_used_at=value.last_used_at,
        created_by=value.created_by,
        created_at=value.created_at,
    )


def _not_found() -> ApiProblem:
    return ApiProblem(
        status_code=404,
        code="service_account_not_found",
        title="Service account not found",
        detail="The requested service account does not exist.",
    )


@router.post("", response_model=ServiceAccountResponse, status_code=201, responses=_RESPONSES)
async def create_service_account(
    payload: CreateServiceAccountRequest,
    request: Request,
    session: AdministratorCsrfSession,
) -> ServiceAccountResponse:
    try:
        value = await _service(request).create(
            actor=session.user,
            name=payload.name,
            role=payload.role,
            request_id=request_id(),
        )
    except ServiceAccountAlreadyExistsError as exc:
        raise ApiProblem(
            status_code=409,
            code="service_account_name_conflict",
            title="Service account name conflict",
            detail="Another service account already uses this name.",
        ) from exc
    except ValueError as exc:
        raise ApiProblem(
            status_code=422,
            code="service_account_invalid",
            title="Service account invalid",
            detail=str(exc),
        ) from exc
    return _account_response(value)


@router.get("", response_model=ServiceAccountCollectionResponse, responses=_RESPONSES)
async def list_service_accounts(
    request: Request,
    session: AdministratorSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> ServiceAccountCollectionResponse:
    values = await _service(request).list(actor=session.user, limit=limit)
    return ServiceAccountCollectionResponse(data=[_account_response(value) for value in values])


@router.get("/{account_id}", response_model=ServiceAccountResponse, responses=_RESPONSES)
async def read_service_account(
    account_id: UUID,
    request: Request,
    session: AdministratorSession,
) -> ServiceAccountResponse:
    try:
        value = await _service(request).get(actor=session.user, account_id=account_id)
    except ServiceAccountNotFoundError as exc:
        raise _not_found() from exc
    return _account_response(value)


@router.patch("/{account_id}", response_model=ServiceAccountResponse, responses=_RESPONSES)
async def update_service_account(
    account_id: UUID,
    payload: UpdateServiceAccountRequest,
    request: Request,
    session: AdministratorCsrfSession,
) -> ServiceAccountResponse:
    try:
        value = await _service(request).update(
            actor=session.user,
            account_id=account_id,
            update=ServiceAccountUpdate(payload.name, payload.role, payload.status),
            request_id=request_id(),
        )
    except ServiceAccountNotFoundError as exc:
        raise _not_found() from exc
    except ServiceAccountAlreadyExistsError as exc:
        raise ApiProblem(
            status_code=409,
            code="service_account_name_conflict",
            title="Service account name conflict",
            detail="Another service account already uses this name.",
        ) from exc
    except ValueError as exc:
        raise ApiProblem(
            status_code=422,
            code="service_account_invalid",
            title="Service account invalid",
            detail=str(exc),
        ) from exc
    return _account_response(value)


@router.post(
    "/{account_id}/tokens",
    response_model=IssuedApiTokenResponse,
    status_code=201,
    responses=_RESPONSES,
)
async def create_api_token(
    account_id: UUID,
    payload: CreateApiTokenRequest,
    request: Request,
    session: AdministratorCsrfSession,
) -> IssuedApiTokenResponse:
    try:
        issued = await _service(request).create_token(
            actor=session.user,
            account_id=account_id,
            name=payload.name,
            scopes=frozenset(payload.scopes),
            expires_at=payload.expires_at,
            request_id=request_id(),
        )
    except ServiceAccountNotFoundError as exc:
        raise _not_found() from exc
    except ValueError as exc:
        raise ApiProblem(
            status_code=422,
            code="api_token_invalid",
            title="API token invalid",
            detail=str(exc),
        ) from exc
    metadata = _token_response(issued.token)
    return IssuedApiTokenResponse(**metadata.model_dump(), token=issued.plaintext)


@router.get(
    "/{account_id}/tokens",
    response_model=ApiTokenCollectionResponse,
    responses=_RESPONSES,
)
async def list_api_tokens(
    account_id: UUID,
    request: Request,
    session: AdministratorSession,
) -> ApiTokenCollectionResponse:
    try:
        values = await _service(request).list_tokens(actor=session.user, account_id=account_id)
    except ServiceAccountNotFoundError as exc:
        raise _not_found() from exc
    return ApiTokenCollectionResponse(data=[_token_response(value) for value in values])


@router.delete(
    "/{account_id}/tokens/{token_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_RESPONSES,
)
async def revoke_api_token(
    account_id: UUID,
    token_id: UUID,
    request: Request,
    session: AdministratorCsrfSession,
) -> Response:
    try:
        await _service(request).revoke_token(
            actor=session.user,
            account_id=account_id,
            token_id=token_id,
            request_id=request_id(),
        )
    except ServiceAccountNotFoundError as exc:
        raise _not_found() from exc
    except ApiTokenNotFoundError as exc:
        raise ApiProblem(
            status_code=404,
            code="api_token_not_found",
            title="API token not found",
            detail="The requested API token does not exist.",
        ) from exc
    return Response(status_code=204)
