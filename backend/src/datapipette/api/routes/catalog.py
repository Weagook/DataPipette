"""Authorized endpoints for live PostgreSQL catalog discovery."""

from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Path, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from datapipette.api.dependencies.authentication import CurrentSession
from datapipette.api.problems import ApiProblem, ProblemDetails
from datapipette.application.catalog import (
    CatalogConnectionNotFoundError,
    CatalogObjectNotFoundError,
    CatalogSecretUnavailableError,
    CatalogService,
    InvalidCatalogCursorError,
    SourceCatalogUnavailableError,
)
from datapipette.domain.catalog import (
    CatalogJoinSuggestion,
    CatalogRelation,
    CatalogRelationDetail,
    CatalogRelationReference,
    RelationKind,
)

router = APIRouter(prefix="/connections/{connection_id}/catalog", tags=["catalog"])

CatalogName = Annotated[str, Path(min_length=1, max_length=255)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CursorMeta(BaseModel):
    next_cursor: str | None


class CatalogSchemaResponse(BaseModel):
    name: str


class CatalogSchemaCollectionResponse(BaseModel):
    data: list[CatalogSchemaResponse]
    meta: CursorMeta


class CatalogRelationResponse(BaseModel):
    schema_name: str
    name: str
    kind: RelationKind


class CatalogRelationCollectionResponse(BaseModel):
    data: list[CatalogRelationResponse]
    meta: CursorMeta


class CatalogColumnResponse(BaseModel):
    name: str
    ordinal_position: int
    data_type: str
    nullable: bool


class CatalogForeignKeyResponse(BaseModel):
    name: str
    source_columns: list[str]
    target_schema: str
    target_relation: str
    target_columns: list[str]


class CatalogRelationDetailResponse(CatalogRelationResponse):
    columns: list[CatalogColumnResponse]
    primary_key: list[str]
    foreign_keys: list[CatalogForeignKeyResponse]


class CatalogJoinRelationPayload(StrictModel):
    id: UUID
    schema_name: str = Field(alias="schema", min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)


class CatalogJoinSuggestionRequest(StrictModel):
    relations: list[CatalogJoinRelationPayload] = Field(min_length=1, max_length=10)


class CatalogJoinConditionResponse(BaseModel):
    source_column: str
    target_column: str


class CatalogJoinSuggestionResponse(BaseModel):
    foreign_key_name: str
    source_relation_id: UUID
    target_relation_id: UUID
    conditions: list[CatalogJoinConditionResponse]


class CatalogJoinSuggestionCollectionResponse(BaseModel):
    data: list[CatalogJoinSuggestionResponse]


def _service(request: Request) -> CatalogService:
    return cast(CatalogService, request.app.state.catalog_service)


def _relation_response(value: CatalogRelation) -> CatalogRelationResponse:
    return CatalogRelationResponse(
        schema_name=value.schema_name,
        name=value.name,
        kind=value.kind,
    )


def _detail_response(value: CatalogRelationDetail) -> CatalogRelationDetailResponse:
    return CatalogRelationDetailResponse(
        schema_name=value.relation.schema_name,
        name=value.relation.name,
        kind=value.relation.kind,
        columns=[
            CatalogColumnResponse(
                name=column.name,
                ordinal_position=column.ordinal_position,
                data_type=column.data_type,
                nullable=column.nullable,
            )
            for column in value.columns
        ],
        primary_key=value.primary_key,
        foreign_keys=[
            CatalogForeignKeyResponse(
                name=foreign_key.name,
                source_columns=foreign_key.source_columns,
                target_schema=foreign_key.target_schema,
                target_relation=foreign_key.target_relation,
                target_columns=foreign_key.target_columns,
            )
            for foreign_key in value.foreign_keys
        ],
    )


def _suggestion_response(value: CatalogJoinSuggestion) -> CatalogJoinSuggestionResponse:
    return CatalogJoinSuggestionResponse(
        foreign_key_name=value.foreign_key_name,
        source_relation_id=value.source_relation_id,
        target_relation_id=value.target_relation_id,
        conditions=[
            CatalogJoinConditionResponse(
                source_column=condition.source_column,
                target_column=condition.target_column,
            )
            for condition in value.conditions
        ],
    )


def _translate_error(exc: Exception) -> ApiProblem:
    if isinstance(exc, CatalogConnectionNotFoundError):
        return ApiProblem(
            status_code=status.HTTP_404_NOT_FOUND,
            code="connection_not_found",
            title="Connection not found",
            detail="The requested PostgreSQL connection does not exist.",
        )
    if isinstance(exc, CatalogObjectNotFoundError):
        return ApiProblem(
            status_code=status.HTTP_404_NOT_FOUND,
            code="catalog_object_not_found",
            title="Catalog object not found",
            detail="The requested source object does not exist or is not visible.",
        )
    if isinstance(exc, InvalidCatalogCursorError):
        return ApiProblem(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="invalid_cursor",
            title="Invalid pagination cursor",
            detail="The supplied catalog pagination cursor is invalid.",
        )
    if isinstance(exc, CatalogSecretUnavailableError):
        return ApiProblem(
            status_code=status.HTTP_409_CONFLICT,
            code="connection_secret_unavailable",
            title="Connection secret unavailable",
            detail="Replace the stored connection secrets before using this connection.",
        )
    return ApiProblem(
        status_code=status.HTTP_502_BAD_GATEWAY,
        code="source_catalog_unavailable",
        title="Source catalog unavailable",
        detail="The PostgreSQL source catalog could not be read.",
    )


_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ProblemDetails},
    401: {"model": ProblemDetails},
    403: {"model": ProblemDetails},
    404: {"model": ProblemDetails},
    409: {"model": ProblemDetails},
    422: {"model": ProblemDetails},
    502: {"model": ProblemDetails},
}


@router.post(
    "/join-suggestions",
    response_model=CatalogJoinSuggestionCollectionResponse,
    responses=_ERROR_RESPONSES,
)
async def suggest_catalog_joins(
    connection_id: UUID,
    payload: CatalogJoinSuggestionRequest,
    request: Request,
    session: CurrentSession,
) -> CatalogJoinSuggestionCollectionResponse:
    try:
        suggestions = await _service(request).suggest_joins(
            actor=session.user,
            connection_id=connection_id,
            relations=tuple(
                CatalogRelationReference(
                    id=relation.id,
                    schema_name=relation.schema_name,
                    relation_name=relation.name,
                )
                for relation in payload.relations
            ),
        )
    except ValueError as exc:
        raise ApiProblem(
            status_code=422,
            code="catalog_join_suggestion_invalid",
            title="Join suggestion request invalid",
            detail="The requested relation references are inconsistent.",
        ) from exc
    except (
        CatalogConnectionNotFoundError,
        CatalogObjectNotFoundError,
        CatalogSecretUnavailableError,
        SourceCatalogUnavailableError,
    ) as exc:
        raise _translate_error(exc) from exc
    return CatalogJoinSuggestionCollectionResponse(
        data=[_suggestion_response(suggestion) for suggestion in suggestions]
    )


@router.get(
    "/schemas",
    response_model=CatalogSchemaCollectionResponse,
    responses=_ERROR_RESPONSES,
)
async def list_catalog_schemas(
    connection_id: UUID,
    request: Request,
    session: CurrentSession,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    include_system: bool = False,
) -> CatalogSchemaCollectionResponse:
    try:
        page = await _service(request).list_schemas(
            actor=session.user,
            connection_id=connection_id,
            cursor=cursor,
            limit=limit,
            include_system=include_system,
        )
    except (
        CatalogConnectionNotFoundError,
        CatalogSecretUnavailableError,
        InvalidCatalogCursorError,
        SourceCatalogUnavailableError,
    ) as exc:
        raise _translate_error(exc) from exc
    return CatalogSchemaCollectionResponse(
        data=[CatalogSchemaResponse(name=name) for name in page.schemas],
        meta=CursorMeta(next_cursor=page.next_cursor),
    )


@router.get(
    "/schemas/{schema_name}/relations",
    response_model=CatalogRelationCollectionResponse,
    responses=_ERROR_RESPONSES,
)
async def list_catalog_relations(
    connection_id: UUID,
    schema_name: CatalogName,
    request: Request,
    session: CurrentSession,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> CatalogRelationCollectionResponse:
    try:
        page = await _service(request).list_relations(
            actor=session.user,
            connection_id=connection_id,
            schema_name=schema_name,
            cursor=cursor,
            limit=limit,
        )
    except (
        CatalogConnectionNotFoundError,
        CatalogSecretUnavailableError,
        InvalidCatalogCursorError,
        SourceCatalogUnavailableError,
    ) as exc:
        raise _translate_error(exc) from exc
    return CatalogRelationCollectionResponse(
        data=[_relation_response(relation) for relation in page.relations],
        meta=CursorMeta(next_cursor=page.next_cursor),
    )


@router.get(
    "/schemas/{schema_name}/relations/{relation_name}",
    response_model=CatalogRelationDetailResponse,
    responses=_ERROR_RESPONSES,
)
async def inspect_catalog_relation(
    connection_id: UUID,
    schema_name: CatalogName,
    relation_name: CatalogName,
    request: Request,
    session: CurrentSession,
) -> CatalogRelationDetailResponse:
    try:
        detail = await _service(request).inspect_relation(
            actor=session.user,
            connection_id=connection_id,
            schema_name=schema_name,
            relation_name=relation_name,
        )
    except (
        CatalogConnectionNotFoundError,
        CatalogObjectNotFoundError,
        CatalogSecretUnavailableError,
        SourceCatalogUnavailableError,
    ) as exc:
        raise _translate_error(exc) from exc
    return _detail_response(detail)
