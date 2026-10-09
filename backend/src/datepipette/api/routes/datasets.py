"""Administrator endpoints for private visual dataset drafts."""

import re
from datetime import datetime
from typing import Annotated, Any, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from datepipette.api.dependencies.authentication import (
    AdministratorCsrfSession,
    AdministratorSession,
    request_id,
)
from datepipette.api.problems import ApiProblem, ProblemDetails
from datepipette.application.dataset_validation import (
    DatasetValidationService,
    SourceDatasetValidationUnavailableError,
)
from datepipette.application.datasets import (
    DatasetAlreadyExistsError,
    DatasetConnectionNotFoundError,
    DatasetCreate,
    DatasetNotFoundError,
    DatasetRevisionConflictError,
    DatasetService,
    InvalidDatasetCursorError,
)
from datepipette.domain.datasets import (
    Dataset,
    DatasetDraft,
    DatasetValidation,
    DatasetValidationState,
    FieldDisplay,
    FieldDisplayFormat,
    FieldSource,
    ValidationFindingCode,
    ValidationSeverity,
    VisualDatasetDefinition,
    VisualField,
    VisualRelation,
)

router = APIRouter(prefix="/datasets", tags=["datasets"])
_ETAG = re.compile(r'^"draft-([1-9][0-9]*)"$')


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VisualRelationPayload(StrictModel):
    id: UUID
    schema_name: str = Field(alias="schema", min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    alias: str = Field(min_length=1, max_length=100)


class VisualSourcePayload(StrictModel):
    relations: list[VisualRelationPayload] = Field(min_length=1, max_length=1)


class FieldSourcePayload(StrictModel):
    relation_id: UUID
    column: str = Field(min_length=1, max_length=255)


class FieldDisplayPayload(StrictModel):
    label: str = Field(min_length=1, max_length=100)
    format: FieldDisplayFormat | None = None
    visible: bool = True


class VisualFieldPayload(StrictModel):
    id: UUID
    source: FieldSourcePayload
    name: str = Field(min_length=1, max_length=100)
    display: FieldDisplayPayload


class VisualDefinitionPayload(StrictModel):
    schema_version: Literal[1]
    source: VisualSourcePayload
    fields: list[VisualFieldPayload] = Field(min_length=1, max_length=500)
    joins: list[dict[str, Any]] = Field(default_factory=list, max_length=0)


class CreateDatasetRequest(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    connection_id: UUID
    definition: VisualDefinitionPayload

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Value must not be empty")
        return normalized


class UpdateDatasetRequest(StrictModel):
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Value must not be empty")
        return normalized


class ReplaceDraftRequest(StrictModel):
    definition: VisualDefinitionPayload


class DatasetResponse(BaseModel):
    id: UUID
    name: str
    owner_id: UUID
    connection_id: UUID
    active_version_id: UUID | None
    draft_revision: int
    created_at: datetime
    updated_at: datetime


class ValidationFindingResponse(BaseModel):
    code: ValidationFindingCode
    severity: ValidationSeverity
    field_id: UUID | None


class ValidatedFieldResponse(BaseModel):
    field_id: UUID
    name: str
    data_type: str
    nullable: bool


class PlanSummaryResponse(BaseModel):
    node_type: str
    estimated_rows: int
    startup_cost: float
    total_cost: float


class DatasetValidationResponse(BaseModel):
    state: DatasetValidationState
    validated_revision: int
    validated_at: datetime
    compiled_sql: str | None
    result_schema: list[ValidatedFieldResponse]
    plan: PlanSummaryResponse | None
    findings: list[ValidationFindingResponse]


class DatasetDraftResponse(BaseModel):
    id: UUID
    dataset_id: UUID
    revision: int
    definition: VisualDefinitionPayload
    validation: DatasetValidationResponse | None
    created_by: UUID
    created_at: datetime
    updated_at: datetime


class CursorMeta(BaseModel):
    next_cursor: str | None


class DatasetCollectionResponse(BaseModel):
    data: list[DatasetResponse]
    meta: CursorMeta


def _service(request: Request) -> DatasetService:
    return cast(DatasetService, request.app.state.dataset_service)


def _validation_service(request: Request) -> DatasetValidationService:
    return cast(DatasetValidationService, request.app.state.dataset_validation_service)


def _definition(payload: VisualDefinitionPayload) -> VisualDatasetDefinition:
    relation_value = payload.source.relations[0]
    relation = VisualRelation(
        id=relation_value.id,
        schema_name=relation_value.schema_name,
        relation_name=relation_value.name,
        alias=relation_value.alias,
    )
    return VisualDatasetDefinition(
        schema_version=payload.schema_version,
        relation=relation,
        fields=tuple(
            VisualField(
                id=field.id,
                source=FieldSource(
                    relation_id=field.source.relation_id,
                    column=field.source.column,
                ),
                name=field.name,
                display=FieldDisplay(
                    label=field.display.label,
                    format=field.display.format,
                    visible=field.display.visible,
                ),
            )
            for field in payload.fields
        ),
    )


def _dataset_response(value: Dataset) -> DatasetResponse:
    return DatasetResponse(
        id=value.id,
        name=value.name,
        owner_id=value.owner_id,
        connection_id=value.connection_id,
        active_version_id=value.active_version_id,
        draft_revision=value.draft_revision,
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


def _draft_response(value: DatasetDraft) -> DatasetDraftResponse:
    return DatasetDraftResponse(
        id=value.id,
        dataset_id=value.dataset_id,
        revision=value.revision,
        definition=VisualDefinitionPayload.model_validate(value.definition.to_document()),
        validation=_validation_response(value.validation) if value.validation else None,
        created_by=value.created_by,
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


def _validation_response(value: DatasetValidation) -> DatasetValidationResponse:
    return DatasetValidationResponse(
        state=value.state,
        validated_revision=value.validated_revision,
        validated_at=value.validated_at,
        compiled_sql=value.compiled_sql,
        result_schema=[
            ValidatedFieldResponse(
                field_id=field.field_id,
                name=field.name,
                data_type=field.data_type,
                nullable=field.nullable,
            )
            for field in value.result_schema
        ],
        plan=(
            PlanSummaryResponse(
                node_type=value.plan.node_type,
                estimated_rows=value.plan.estimated_rows,
                startup_cost=value.plan.startup_cost,
                total_cost=value.plan.total_cost,
            )
            if value.plan
            else None
        ),
        findings=[
            ValidationFindingResponse(
                code=finding.code,
                severity=finding.severity,
                field_id=finding.field_id,
            )
            for finding in value.findings
        ],
    )


def _etag(revision: int) -> str:
    return f'"draft-{revision}"'


def _expected_revision(value: str | None) -> int:
    if value is None:
        raise ApiProblem(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            code="draft_revision_required",
            title="Draft revision required",
            detail="Supply the current draft ETag in the If-Match header.",
        )
    match = _ETAG.fullmatch(value)
    if match is None:
        raise ApiProblem(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="invalid_draft_revision",
            title="Invalid draft revision",
            detail="The If-Match header is not a valid dataset draft ETag.",
        )
    return int(match.group(1))


def _not_found() -> ApiProblem:
    return ApiProblem(
        status_code=404,
        code="dataset_not_found",
        title="Dataset not found",
        detail="The requested dataset does not exist.",
    )


def _invalid_definition() -> ApiProblem:
    return ApiProblem(
        status_code=422,
        code="dataset_definition_invalid",
        title="Dataset definition invalid",
        detail="The visual dataset definition is inconsistent.",
    )


_COMMON_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ProblemDetails},
    403: {"model": ProblemDetails},
    404: {"model": ProblemDetails},
    422: {"model": ProblemDetails},
}
_ETAG_HEADER = {
    "ETag": {
        "description": "Opaque revision of the current dataset draft.",
        "schema": {"type": "string", "example": '"draft-3"'},
    }
}


@router.post(
    "",
    response_model=DatasetResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        **_COMMON_RESPONSES,
        201: {"headers": _ETAG_HEADER},
        409: {"model": ProblemDetails},
    },
)
async def create_dataset(
    payload: CreateDatasetRequest,
    request: Request,
    response: Response,
    session: AdministratorCsrfSession,
) -> DatasetResponse:
    try:
        dataset, draft = await _service(request).create(
            actor=session.user,
            value=DatasetCreate(
                name=payload.name,
                connection_id=payload.connection_id,
                definition=_definition(payload.definition),
            ),
            request_id=request_id(),
        )
    except DatasetAlreadyExistsError as exc:
        raise ApiProblem(
            status_code=409,
            code="dataset_name_conflict",
            title="Dataset name already in use",
            detail="Another dataset owned by this user already uses this name.",
        ) from exc
    except DatasetConnectionNotFoundError as exc:
        raise ApiProblem(
            status_code=404,
            code="connection_not_found",
            title="Connection not found",
            detail="The requested PostgreSQL connection does not exist.",
        ) from exc
    except ValueError as exc:
        raise _invalid_definition() from exc
    response.headers["ETag"] = _etag(draft.revision)
    return _dataset_response(dataset)


@router.get(
    "",
    response_model=DatasetCollectionResponse,
    responses={**_COMMON_RESPONSES, 400: {"model": ProblemDetails}},
)
async def list_datasets(
    request: Request,
    session: AdministratorSession,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> DatasetCollectionResponse:
    try:
        page = await _service(request).list(actor=session.user, cursor=cursor, limit=limit)
    except InvalidDatasetCursorError as exc:
        raise ApiProblem(
            status_code=400,
            code="invalid_cursor",
            title="Invalid pagination cursor",
            detail="The supplied dataset pagination cursor is invalid.",
        ) from exc
    return DatasetCollectionResponse(
        data=[_dataset_response(item) for item in page.datasets],
        meta=CursorMeta(next_cursor=page.next_cursor),
    )


@router.get(
    "/{dataset_id}",
    response_model=DatasetResponse,
    responses=_COMMON_RESPONSES,
)
async def read_dataset(
    dataset_id: UUID,
    request: Request,
    session: AdministratorSession,
) -> DatasetResponse:
    try:
        value = await _service(request).get(actor=session.user, dataset_id=dataset_id)
    except DatasetNotFoundError as exc:
        raise _not_found() from exc
    return _dataset_response(value)


@router.patch(
    "/{dataset_id}",
    response_model=DatasetResponse,
    responses={**_COMMON_RESPONSES, 409: {"model": ProblemDetails}},
)
async def rename_dataset(
    dataset_id: UUID,
    payload: UpdateDatasetRequest,
    request: Request,
    session: AdministratorCsrfSession,
) -> DatasetResponse:
    try:
        value = await _service(request).rename(
            actor=session.user,
            dataset_id=dataset_id,
            name=payload.name,
            request_id=request_id(),
        )
    except DatasetAlreadyExistsError as exc:
        raise ApiProblem(
            status_code=409,
            code="dataset_name_conflict",
            title="Dataset name already in use",
            detail="Another dataset owned by this user already uses this name.",
        ) from exc
    except DatasetNotFoundError as exc:
        raise _not_found() from exc
    return _dataset_response(value)


@router.get(
    "/{dataset_id}/draft",
    response_model=DatasetDraftResponse,
    responses={**_COMMON_RESPONSES, 200: {"headers": _ETAG_HEADER}},
)
async def read_dataset_draft(
    dataset_id: UUID,
    request: Request,
    response: Response,
    session: AdministratorSession,
) -> DatasetDraftResponse:
    try:
        draft = await _service(request).get_draft(actor=session.user, dataset_id=dataset_id)
    except DatasetNotFoundError as exc:
        raise _not_found() from exc
    response.headers["ETag"] = _etag(draft.revision)
    return _draft_response(draft)


@router.put(
    "/{dataset_id}/draft",
    response_model=DatasetDraftResponse,
    responses={
        **_COMMON_RESPONSES,
        200: {"headers": _ETAG_HEADER},
        400: {"model": ProblemDetails},
        409: {"model": ProblemDetails},
        428: {"model": ProblemDetails},
    },
)
async def replace_dataset_draft(
    dataset_id: UUID,
    payload: ReplaceDraftRequest,
    request: Request,
    response: Response,
    session: AdministratorCsrfSession,
    if_match: Annotated[str | None, Header(alias="If-Match", max_length=64)] = None,
) -> DatasetDraftResponse:
    try:
        draft = await _service(request).replace_draft(
            actor=session.user,
            dataset_id=dataset_id,
            expected_revision=_expected_revision(if_match),
            definition=_definition(payload.definition),
            request_id=request_id(),
        )
    except DatasetRevisionConflictError as exc:
        raise ApiProblem(
            status_code=409,
            code="dataset_revision_conflict",
            title="Dataset draft changed",
            detail="Refetch the latest draft before applying this update.",
        ) from exc
    except DatasetNotFoundError as exc:
        raise _not_found() from exc
    except ValueError as exc:
        raise _invalid_definition() from exc
    response.headers["ETag"] = _etag(draft.revision)
    return _draft_response(draft)


@router.post(
    "/{dataset_id}/draft/validations",
    response_model=DatasetValidationResponse,
    responses={
        **_COMMON_RESPONSES,
        400: {"model": ProblemDetails},
        409: {"model": ProblemDetails},
        428: {"model": ProblemDetails},
        502: {"model": ProblemDetails},
    },
)
async def validate_dataset_draft(
    dataset_id: UUID,
    request: Request,
    session: AdministratorCsrfSession,
    if_match: Annotated[str | None, Header(alias="If-Match", max_length=64)] = None,
) -> DatasetValidationResponse:
    try:
        draft = await _validation_service(request).validate(
            actor=session.user,
            dataset_id=dataset_id,
            expected_revision=_expected_revision(if_match),
            request_id=request_id(),
        )
    except DatasetRevisionConflictError as exc:
        raise ApiProblem(
            status_code=409,
            code="dataset_revision_conflict",
            title="Dataset draft changed",
            detail="Refetch the latest draft before validating it.",
        ) from exc
    except DatasetNotFoundError as exc:
        raise _not_found() from exc
    except SourceDatasetValidationUnavailableError as exc:
        raise ApiProblem(
            status_code=502,
            code="source_validation_unavailable",
            title="Source validation unavailable",
            detail="The PostgreSQL source could not be reached or inspected safely.",
        ) from exc
    if draft.validation is None:  # pragma: no cover - persistence contract guard
        raise RuntimeError("Dataset validation was not persisted")
    return _validation_response(draft.validation)
