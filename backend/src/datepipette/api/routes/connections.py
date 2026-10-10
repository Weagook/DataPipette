"""PostgreSQL source connection and administrator grant endpoints."""

from datetime import datetime
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from datepipette.api.dependencies.authentication import (
    AdministratorCsrfSession,
    AdministratorSession,
    CurrentSession,
    request_id,
)
from datepipette.api.problems import ApiProblem, ProblemDetails
from datepipette.application.connection_access import (
    ConnectionAccessDeniedError,
    ConnectionGrantNotFoundError,
    ConnectionGrantPrincipalNotFoundError,
    ConnectionGrantService,
)
from datepipette.application.connections import (
    ConnectionAlreadyExistsError,
    ConnectionCreate,
    ConnectionNotFoundError,
    ConnectionSecretUnavailableError,
    ConnectionService,
    ConnectionUpdate,
    InvalidConnectionCursorError,
)
from datepipette.domain.access_control import GrantPrincipalType
from datepipette.domain.connection_access import (
    ConnectionGrant,
    ConnectionScope,
    ConnectionScopeType,
)
from datepipette.domain.connections import ConnectionTestResult, PostgresConnection, PostgresSslMode

router = APIRouter(prefix="/connections", tags=["connections"])


class CreateConnectionRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=5432, ge=1, le=65535)
    database_name: str = Field(min_length=1, max_length=255)
    username: str = Field(min_length=1, max_length=255)
    password: Annotated[SecretStr, Field(min_length=1, max_length=1024)]
    ssl_mode: PostgresSslMode = PostgresSslMode.VERIFY_FULL
    ca_certificate: Annotated[SecretStr | None, Field(max_length=131_072)] = None

    @field_validator("name", "host", "database_name", "username")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Value must not be empty")
        return normalized


class UpdateConnectionRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    host: str | None = Field(default=None, min_length=1, max_length=253)
    port: int | None = Field(default=None, ge=1, le=65535)
    database_name: str | None = Field(default=None, min_length=1, max_length=255)
    username: str | None = Field(default=None, min_length=1, max_length=255)
    password: Annotated[SecretStr | None, Field(min_length=1, max_length=1024)] = None
    ssl_mode: PostgresSslMode | None = None
    ca_certificate: Annotated[SecretStr | None, Field(max_length=131_072)] = None

    @field_validator("name", "host", "database_name", "username")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Value must not be empty")
        return normalized

    @model_validator(mode="after")
    def require_non_null_change(self) -> "UpdateConnectionRequest":
        non_ca_fields = self.model_fields_set - {"ca_certificate"}
        if not self.model_fields_set or (
            not non_ca_fields and "ca_certificate" not in self.model_fields_set
        ):
            raise ValueError("At least one field must be provided")
        if any(getattr(self, field) is None for field in non_ca_fields):
            raise ValueError("Connection fields other than CA certificate cannot be null")
        return self


class ConnectionResponse(BaseModel):
    id: UUID
    name: str
    host: str
    port: int
    database_name: str
    username: str
    ssl_mode: PostgresSslMode
    has_password: bool = True
    has_ca_certificate: bool
    created_by: UUID
    last_tested_at: datetime | None
    last_test_succeeded: bool | None
    last_test_error_code: str | None
    created_at: datetime
    updated_at: datetime


class CursorMeta(BaseModel):
    next_cursor: str | None


class ConnectionCollectionResponse(BaseModel):
    data: list[ConnectionResponse]
    meta: CursorMeta


class ConnectionTestResponse(BaseModel):
    succeeded: bool
    tested_at: datetime
    error_code: str | None
    server_version: int | None


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConnectionGrantCreateRequest(StrictModel):
    principal_type: GrantPrincipalType
    principal_id: UUID
    scope_type: ConnectionScopeType
    schema_name: str | None = Field(default=None, max_length=255)
    relation_name: str | None = Field(default=None, max_length=255)


class ConnectionGrantResponse(BaseModel):
    id: UUID
    connection_id: UUID
    principal_type: GrantPrincipalType
    principal_id: UUID
    scope_type: ConnectionScopeType
    schema_name: str | None
    relation_name: str | None
    created_by: UUID
    created_at: datetime


class ConnectionGrantCollectionResponse(BaseModel):
    data: list[ConnectionGrantResponse]


def _service(request: Request) -> ConnectionService:
    return cast(ConnectionService, request.app.state.connection_service)


def _grant_service(request: Request) -> ConnectionGrantService:
    return cast(ConnectionGrantService, request.app.state.connection_grant_service)


def _response(value: PostgresConnection) -> ConnectionResponse:
    return ConnectionResponse(
        id=value.id,
        name=value.name,
        host=value.host,
        port=value.port,
        database_name=value.database_name,
        username=value.username,
        ssl_mode=value.ssl_mode,
        has_ca_certificate=value.has_ca_certificate,
        created_by=value.created_by,
        last_tested_at=value.last_tested_at,
        last_test_succeeded=value.last_test_succeeded,
        last_test_error_code=value.last_test_error_code,
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


def _test_response(value: ConnectionTestResult) -> ConnectionTestResponse:
    return ConnectionTestResponse(
        succeeded=value.succeeded,
        tested_at=value.tested_at,
        error_code=value.error_code,
        server_version=value.server_version,
    )


def _grant_response(value: ConnectionGrant) -> ConnectionGrantResponse:
    return ConnectionGrantResponse(
        id=value.id,
        connection_id=value.connection_id,
        principal_type=value.principal_type,
        principal_id=value.principal_id,
        scope_type=value.scope.type,
        schema_name=value.scope.schema_name,
        relation_name=value.scope.relation_name,
        created_by=value.created_by,
        created_at=value.created_at,
    )


def _not_found() -> ApiProblem:
    return ApiProblem(
        status_code=404,
        code="connection_not_found",
        title="Connection not found",
        detail="The requested PostgreSQL connection does not exist.",
    )


def _conflict() -> ApiProblem:
    return ApiProblem(
        status_code=409,
        code="connection_name_conflict",
        title="Connection name already in use",
        detail="Another PostgreSQL connection already uses this name.",
    )


def _secret_unavailable() -> ApiProblem:
    return ApiProblem(
        status_code=409,
        code="connection_secret_unavailable",
        title="Connection secret unavailable",
        detail="Replace the stored connection secrets before using this connection.",
    )


@router.post(
    "",
    response_model=ConnectionResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        401: {"model": ProblemDetails},
        403: {"model": ProblemDetails},
        409: {"model": ProblemDetails},
        422: {"model": ProblemDetails},
    },
)
async def create_connection(
    payload: CreateConnectionRequest,
    request: Request,
    session: AdministratorCsrfSession,
) -> ConnectionResponse:
    try:
        value = await _service(request).create(
            actor=session.user,
            value=ConnectionCreate(
                name=payload.name,
                host=payload.host,
                port=payload.port,
                database_name=payload.database_name,
                username=payload.username,
                password=payload.password.get_secret_value(),
                ssl_mode=payload.ssl_mode,
                ca_certificate=(
                    payload.ca_certificate.get_secret_value()
                    if payload.ca_certificate is not None
                    else None
                ),
            ),
            request_id=request_id(),
        )
    except ConnectionAlreadyExistsError as exc:
        raise _conflict() from exc
    except ValueError as exc:
        raise ApiProblem(
            status_code=422,
            code="connection_configuration_invalid",
            title="Connection configuration invalid",
            detail="The supplied PostgreSQL connection settings are inconsistent.",
        ) from exc
    return _response(value)


@router.get(
    "",
    response_model=ConnectionCollectionResponse,
    responses={
        400: {"model": ProblemDetails},
        401: {"model": ProblemDetails},
        403: {"model": ProblemDetails},
        422: {"model": ProblemDetails},
    },
)
async def list_connections(
    request: Request,
    session: CurrentSession,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ConnectionCollectionResponse:
    try:
        page = await _service(request).list(actor=session.user, cursor=cursor, limit=limit)
    except InvalidConnectionCursorError as exc:
        raise ApiProblem(
            status_code=400,
            code="invalid_cursor",
            title="Invalid pagination cursor",
            detail="The supplied connection pagination cursor is invalid.",
        ) from exc
    return ConnectionCollectionResponse(
        data=[_response(item) for item in page.connections],
        meta=CursorMeta(next_cursor=page.next_cursor),
    )


@router.get(
    "/{connection_id}",
    response_model=ConnectionResponse,
    responses={
        401: {"model": ProblemDetails},
        403: {"model": ProblemDetails},
        404: {"model": ProblemDetails},
        422: {"model": ProblemDetails},
    },
)
async def read_connection(
    connection_id: UUID,
    request: Request,
    session: CurrentSession,
) -> ConnectionResponse:
    try:
        value = await _service(request).get(actor=session.user, connection_id=connection_id)
    except ConnectionNotFoundError as exc:
        raise _not_found() from exc
    return _response(value)


@router.patch(
    "/{connection_id}",
    response_model=ConnectionResponse,
    responses={
        401: {"model": ProblemDetails},
        403: {"model": ProblemDetails},
        404: {"model": ProblemDetails},
        409: {"model": ProblemDetails},
        422: {"model": ProblemDetails},
    },
)
async def update_connection(
    connection_id: UUID,
    payload: UpdateConnectionRequest,
    request: Request,
    session: AdministratorCsrfSession,
) -> ConnectionResponse:
    try:
        value = await _service(request).update(
            actor=session.user,
            connection_id=connection_id,
            value=ConnectionUpdate(
                name=payload.name,
                host=payload.host,
                port=payload.port,
                database_name=payload.database_name,
                username=payload.username,
                password=(
                    payload.password.get_secret_value() if payload.password is not None else None
                ),
                ssl_mode=payload.ssl_mode,
                ca_certificate=(
                    payload.ca_certificate.get_secret_value()
                    if payload.ca_certificate is not None
                    else None
                ),
                ca_certificate_set="ca_certificate" in payload.model_fields_set,
            ),
            request_id=request_id(),
        )
    except ConnectionNotFoundError as exc:
        raise _not_found() from exc
    except ConnectionAlreadyExistsError as exc:
        raise _conflict() from exc
    except ConnectionSecretUnavailableError as exc:
        raise _secret_unavailable() from exc
    except ValueError as exc:
        raise ApiProblem(
            status_code=422,
            code="connection_configuration_invalid",
            title="Connection configuration invalid",
            detail="The supplied PostgreSQL connection settings are inconsistent.",
        ) from exc
    return _response(value)


@router.post(
    "/{connection_id}/tests",
    response_model=ConnectionTestResponse,
    responses={
        401: {"model": ProblemDetails},
        403: {"model": ProblemDetails},
        404: {"model": ProblemDetails},
        422: {"model": ProblemDetails},
    },
)
async def test_connection(
    connection_id: UUID,
    request: Request,
    session: AdministratorCsrfSession,
) -> ConnectionTestResponse:
    try:
        result = await _service(request).test(
            actor=session.user,
            connection_id=connection_id,
            request_id=request_id(),
        )
    except ConnectionNotFoundError as exc:
        raise _not_found() from exc
    return _test_response(result)


@router.get(
    "/{connection_id}/grants",
    response_model=ConnectionGrantCollectionResponse,
    responses={
        401: {"model": ProblemDetails},
        403: {"model": ProblemDetails},
        404: {"model": ProblemDetails},
    },
)
async def list_connection_grants(
    connection_id: UUID,
    request: Request,
    session: AdministratorSession,
) -> ConnectionGrantCollectionResponse:
    try:
        values = await _grant_service(request).list(
            actor=session.user,
            connection_id=connection_id,
        )
    except ConnectionAccessDeniedError as exc:
        raise _not_found() from exc
    return ConnectionGrantCollectionResponse(data=[_grant_response(value) for value in values])


@router.post(
    "/{connection_id}/grants",
    response_model=ConnectionGrantResponse,
    status_code=201,
    responses={
        401: {"model": ProblemDetails},
        403: {"model": ProblemDetails},
        404: {"model": ProblemDetails},
        422: {"model": ProblemDetails},
    },
)
async def create_connection_grant(
    connection_id: UUID,
    payload: ConnectionGrantCreateRequest,
    request: Request,
    session: AdministratorCsrfSession,
) -> ConnectionGrantResponse:
    try:
        value = await _grant_service(request).create(
            actor=session.user,
            connection_id=connection_id,
            principal_type=payload.principal_type,
            principal_id=payload.principal_id,
            scope=ConnectionScope(
                payload.scope_type,
                payload.schema_name,
                payload.relation_name,
            ),
            request_id=request_id(),
        )
    except ConnectionGrantPrincipalNotFoundError as exc:
        raise ApiProblem(
            status_code=404,
            code="connection_or_principal_not_found",
            title="Connection or principal not found",
            detail="The connection, active analyst, or group does not exist.",
        ) from exc
    except ValueError as exc:
        raise ApiProblem(
            status_code=422,
            code="connection_scope_invalid",
            title="Connection scope invalid",
            detail=str(exc),
        ) from exc
    return _grant_response(value)


@router.delete(
    "/{connection_id}/grants/{grant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        401: {"model": ProblemDetails},
        403: {"model": ProblemDetails},
        404: {"model": ProblemDetails},
    },
)
async def revoke_connection_grant(
    connection_id: UUID,
    grant_id: UUID,
    request: Request,
    session: AdministratorCsrfSession,
) -> Response:
    try:
        await _grant_service(request).revoke(
            actor=session.user,
            connection_id=connection_id,
            grant_id=grant_id,
            request_id=request_id(),
        )
    except ConnectionAccessDeniedError as exc:
        raise _not_found() from exc
    except ConnectionGrantNotFoundError as exc:
        raise ApiProblem(
            status_code=404,
            code="connection_grant_not_found",
            title="Connection grant not found",
            detail="The requested connection grant does not exist.",
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
