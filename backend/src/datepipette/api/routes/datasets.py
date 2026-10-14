"""Owner-authorized endpoints for private dataset drafts."""

from datetime import datetime
from typing import Annotated, Any, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from datepipette.api.dependencies.authentication import (
    CsrfSession,
    CurrentSession,
    request_id,
)
from datepipette.api.draft_revisions import draft_etag, expected_draft_revision
from datepipette.api.problems import ApiProblem, ProblemDetails
from datepipette.application.dataset_preview import (
    MAX_PREVIEW_ROWS,
    DatasetDraftNotValidatedError,
    DatasetPreviewService,
    PreviewPolicyLimitExceededError,
    SourceDatasetPreviewUnavailableError,
)
from datepipette.application.dataset_validation import (
    DatasetValidationService,
    QuerySafetyOverrideNotApplicableError,
    SourceDatasetValidationUnavailableError,
)
from datepipette.application.datasets import (
    DatasetAlreadyExistsError,
    DatasetConnectionNotFoundError,
    DatasetCreate,
    DatasetDraftImpactNotValidatedError,
    DatasetNotFoundError,
    DatasetRevisionConflictError,
    DatasetService,
    InvalidDatasetCursorError,
)
from datepipette.application.query_safety import QuerySafetyAdministratorRequiredError
from datepipette.domain.dataset_impact import (
    DatasetDependencyReference,
    DatasetSchemaImpact,
    DependencyResourceType,
    ImpactField,
    SchemaChangeCompatibility,
    SchemaChangeKind,
)
from datepipette.domain.datasets import (
    Dataset,
    DatasetDefinition,
    DatasetDefinitionKind,
    DatasetDraft,
    DatasetPreview,
    DatasetValidation,
    DatasetValidationState,
    DatasetVersion,
    DatasetVersionState,
    FieldDisplay,
    FieldDisplayFormat,
    FieldSource,
    PreviewTruncationReason,
    SqlDatasetDefinition,
    SqlField,
    ValidationFindingCode,
    ValidationSeverity,
    VisualDatasetDefinition,
    VisualField,
    VisualJoin,
    VisualJoinCondition,
    VisualJoinType,
    VisualRelation,
)

router = APIRouter(prefix="/datasets", tags=["datasets"])


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VisualRelationPayload(StrictModel):
    id: UUID
    schema_name: str = Field(alias="schema", min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    alias: str = Field(min_length=1, max_length=100)


class VisualSourcePayload(StrictModel):
    relations: list[VisualRelationPayload] = Field(min_length=1, max_length=10)


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


class VisualJoinConditionPayload(StrictModel):
    left_column: str = Field(min_length=1, max_length=255)
    right_column: str = Field(min_length=1, max_length=255)


class VisualJoinPayload(StrictModel):
    id: UUID
    type: VisualJoinType
    left_relation_id: UUID
    right_relation_id: UUID
    conditions: list[VisualJoinConditionPayload] = Field(min_length=1, max_length=8)


class VisualDefinitionPayload(StrictModel):
    kind: Literal[DatasetDefinitionKind.VISUAL] = DatasetDefinitionKind.VISUAL
    schema_version: Literal[1, 2]
    source: VisualSourcePayload
    fields: list[VisualFieldPayload] = Field(min_length=1, max_length=500)
    joins: list[VisualJoinPayload] = Field(default_factory=list, max_length=9)


class SqlFieldPayload(StrictModel):
    id: UUID
    name: str = Field(min_length=1, max_length=100)
    display: FieldDisplayPayload


class SqlDefinitionPayload(StrictModel):
    kind: Literal[DatasetDefinitionKind.SQL]
    schema_version: Literal[1]
    statement: str = Field(min_length=1, max_length=65_536)
    fields: list[SqlFieldPayload] = Field(min_length=1, max_length=500)


DefinitionPayload = VisualDefinitionPayload | SqlDefinitionPayload


class CreateDatasetRequest(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    connection_id: UUID
    definition: DefinitionPayload

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
    definition: DefinitionPayload


class PreviewDatasetDraftRequest(StrictModel):
    limit: int = Field(default=100, ge=1, le=MAX_PREVIEW_ROWS)


class ValidateDatasetDraftRequest(StrictModel):
    override_reason: str = Field(min_length=10, max_length=500)


class DatasetResponse(BaseModel):
    id: UUID
    name: str
    owner_id: UUID
    connection_id: UUID
    active_version_id: UUID | None
    active_snapshot_id: UUID | None
    draft_revision: int
    created_at: datetime
    updated_at: datetime


class ValidationFindingResponse(BaseModel):
    code: ValidationFindingCode
    severity: ValidationSeverity
    field_id: UUID | None
    relation_id: UUID | None
    join_id: UUID | None
    observed_value: float | None
    threshold_value: float | None


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
    node_count: int
    join_count: int
    sort_count: int
    max_plan_rows: int
    max_sequential_scan_rows: int
    max_sort_rows: int


class RelationStatisticsResponse(BaseModel):
    relation_id: UUID | None
    schema_name: str
    relation_name: str
    approximate_rows: int
    relation_bytes: int
    index_bytes: int
    visible_index_count: int
    statistics_updated_at: datetime | None


class QuerySafetyOverrideResponse(BaseModel):
    actor_id: UUID
    reason: str
    occurred_at: datetime
    finding_codes: list[ValidationFindingCode]


class DatasetValidationResponse(BaseModel):
    state: DatasetValidationState
    validated_revision: int
    validated_at: datetime
    compiled_sql: str | None
    result_schema: list[ValidatedFieldResponse]
    plan: PlanSummaryResponse | None
    findings: list[ValidationFindingResponse]
    relation_statistics: list[RelationStatisticsResponse]
    policy_revision: int
    query_safety_override: QuerySafetyOverrideResponse | None


class DatasetDraftResponse(BaseModel):
    id: UUID
    dataset_id: UUID
    revision: int
    definition: DefinitionPayload
    validation: DatasetValidationResponse | None
    created_by: UUID
    created_at: datetime
    updated_at: datetime


class DatasetPreviewResponse(BaseModel):
    revision: int
    policy_revision: int
    columns: list[ValidatedFieldResponse]
    rows: list[dict[str, Any]]
    requested_limit: int
    returned_rows: int
    truncated: bool
    truncation_reason: PreviewTruncationReason | None


class CursorMeta(BaseModel):
    next_cursor: str | None


class DatasetCollectionResponse(BaseModel):
    data: list[DatasetResponse]
    meta: CursorMeta


class DatasetVersionResponse(BaseModel):
    id: UUID
    dataset_id: UUID
    state: DatasetVersionState
    revision: int
    definition: DefinitionPayload
    validation: DatasetValidationResponse | None
    created_by: UUID
    created_at: datetime
    updated_at: datetime


class DatasetVersionCollectionResponse(BaseModel):
    data: list[DatasetVersionResponse]
    meta: CursorMeta


class ImpactFieldResponse(BaseModel):
    field_id: UUID
    name: str
    data_type: str | None
    nullable: bool | None


class DatasetSchemaChangeResponse(BaseModel):
    kind: SchemaChangeKind
    compatibility: SchemaChangeCompatibility
    field_id: UUID
    before: ImpactFieldResponse | None
    after: ImpactFieldResponse | None


class DatasetDependencyResponse(BaseModel):
    resource_type: DependencyResourceType
    resource_id: UUID


class DatasetImpactResponse(BaseModel):
    dataset_id: UUID
    baseline_version_id: UUID | None
    draft_version_id: UUID
    draft_revision: int
    draft_validation_state: DatasetValidationState
    ready_for_publication: bool
    breaking_change_count: int
    changes: list[DatasetSchemaChangeResponse]
    affected_dependencies: list[DatasetDependencyResponse]
    blocking_dependencies: list[DatasetDependencyResponse]


def _service(request: Request) -> DatasetService:
    return cast(DatasetService, request.app.state.dataset_service)


def _validation_service(request: Request) -> DatasetValidationService:
    return cast(DatasetValidationService, request.app.state.dataset_validation_service)


def _preview_service(request: Request) -> DatasetPreviewService:
    return cast(DatasetPreviewService, request.app.state.dataset_preview_service)


def _definition(payload: DefinitionPayload) -> DatasetDefinition:
    if isinstance(payload, SqlDefinitionPayload):
        return SqlDatasetDefinition(
            schema_version=payload.schema_version,
            statement=payload.statement,
            fields=tuple(
                SqlField(
                    id=item.id,
                    name=item.name,
                    display=FieldDisplay(
                        label=item.display.label,
                        format=item.display.format,
                        visible=item.display.visible,
                    ),
                )
                for item in payload.fields
            ),
        )
    return VisualDatasetDefinition(
        schema_version=payload.schema_version,
        relations=tuple(
            VisualRelation(
                id=relation.id,
                schema_name=relation.schema_name,
                relation_name=relation.name,
                alias=relation.alias,
            )
            for relation in payload.source.relations
        ),
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
        joins=tuple(
            VisualJoin(
                id=join.id,
                type=join.type,
                left_relation_id=join.left_relation_id,
                right_relation_id=join.right_relation_id,
                conditions=tuple(
                    VisualJoinCondition(
                        left_column=condition.left_column,
                        right_column=condition.right_column,
                    )
                    for condition in join.conditions
                ),
            )
            for join in payload.joins
        ),
    )


def _dataset_response(value: Dataset) -> DatasetResponse:
    return DatasetResponse(
        id=value.id,
        name=value.name,
        owner_id=value.owner_id,
        connection_id=value.connection_id,
        active_version_id=value.active_version_id,
        active_snapshot_id=value.active_snapshot_id,
        draft_revision=value.draft_revision,
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


def _draft_response(value: DatasetDraft) -> DatasetDraftResponse:
    document = value.definition.to_document()
    definition = (
        SqlDefinitionPayload.model_validate(document)
        if isinstance(value.definition, SqlDatasetDefinition)
        else VisualDefinitionPayload.model_validate(document)
    )
    return DatasetDraftResponse(
        id=value.id,
        dataset_id=value.dataset_id,
        revision=value.revision,
        definition=definition,
        validation=_validation_response(value.validation) if value.validation else None,
        created_by=value.created_by,
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


def _version_response(value: DatasetVersion) -> DatasetVersionResponse:
    document = value.definition.to_document()
    definition = (
        SqlDefinitionPayload.model_validate(document)
        if isinstance(value.definition, SqlDatasetDefinition)
        else VisualDefinitionPayload.model_validate(document)
    )
    return DatasetVersionResponse(
        id=value.id,
        dataset_id=value.dataset_id,
        state=value.state,
        revision=value.revision,
        definition=definition,
        validation=_validation_response(value.validation) if value.validation else None,
        created_by=value.created_by,
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


def _impact_field_response(value: ImpactField | None) -> ImpactFieldResponse | None:
    if value is None:
        return None
    return ImpactFieldResponse(
        field_id=value.field_id,
        name=value.name,
        data_type=value.data_type,
        nullable=value.nullable,
    )


def _dependency_response(value: DatasetDependencyReference) -> DatasetDependencyResponse:
    return DatasetDependencyResponse(
        resource_type=value.resource_type,
        resource_id=value.resource_id,
    )


def _impact_response(value: DatasetSchemaImpact) -> DatasetImpactResponse:
    return DatasetImpactResponse(
        dataset_id=value.dataset_id,
        baseline_version_id=value.baseline_version_id,
        draft_version_id=value.draft_version_id,
        draft_revision=value.draft_revision,
        draft_validation_state=value.draft_validation_state,
        ready_for_publication=value.ready_for_publication,
        breaking_change_count=value.breaking_change_count,
        changes=[
            DatasetSchemaChangeResponse(
                kind=change.kind,
                compatibility=change.compatibility,
                field_id=change.field_id,
                before=_impact_field_response(change.before),
                after=_impact_field_response(change.after),
            )
            for change in value.changes
        ],
        affected_dependencies=[_dependency_response(item) for item in value.affected_dependencies],
        blocking_dependencies=[_dependency_response(item) for item in value.blocking_dependencies],
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
                node_count=value.plan.node_count,
                join_count=value.plan.join_count,
                sort_count=value.plan.sort_count,
                max_plan_rows=value.plan.max_plan_rows,
                max_sequential_scan_rows=value.plan.max_sequential_scan_rows,
                max_sort_rows=value.plan.max_sort_rows,
            )
            if value.plan
            else None
        ),
        findings=[
            ValidationFindingResponse(
                code=finding.code,
                severity=finding.severity,
                field_id=finding.field_id,
                relation_id=finding.relation_id,
                join_id=finding.join_id,
                observed_value=finding.observed_value,
                threshold_value=finding.threshold_value,
            )
            for finding in value.findings
        ],
        relation_statistics=[
            RelationStatisticsResponse(
                relation_id=item.relation_id,
                schema_name=item.schema_name,
                relation_name=item.relation_name,
                approximate_rows=item.approximate_rows,
                relation_bytes=item.relation_bytes,
                index_bytes=item.index_bytes,
                visible_index_count=item.visible_index_count,
                statistics_updated_at=item.statistics_updated_at,
            )
            for item in value.relation_statistics
        ],
        policy_revision=value.policy_revision,
        query_safety_override=(
            QuerySafetyOverrideResponse(
                actor_id=value.query_safety_override.actor_id,
                reason=value.query_safety_override.reason,
                occurred_at=value.query_safety_override.occurred_at,
                finding_codes=list(value.query_safety_override.finding_codes),
            )
            if value.query_safety_override is not None
            else None
        ),
    )


def _preview_response(value: DatasetPreview) -> DatasetPreviewResponse:
    return DatasetPreviewResponse(
        revision=value.revision,
        policy_revision=value.policy_revision,
        columns=[
            ValidatedFieldResponse(
                field_id=field.field_id,
                name=field.name,
                data_type=field.data_type,
                nullable=field.nullable,
            )
            for field in value.columns
        ],
        rows=list(value.rows),
        requested_limit=value.requested_limit,
        returned_rows=value.returned_rows,
        truncated=value.truncated,
        truncation_reason=value.truncation_reason,
    )


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
        detail="The dataset definition is inconsistent or unsafe.",
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
    session: CsrfSession,
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
    response.headers["ETag"] = draft_etag(draft.revision)
    return _dataset_response(dataset)


@router.get(
    "",
    response_model=DatasetCollectionResponse,
    responses={**_COMMON_RESPONSES, 400: {"model": ProblemDetails}},
)
async def list_datasets(
    request: Request,
    session: CurrentSession,
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
    session: CurrentSession,
) -> DatasetResponse:
    try:
        value = await _service(request).get(actor=session.user, dataset_id=dataset_id)
    except DatasetNotFoundError as exc:
        raise _not_found() from exc
    return _dataset_response(value)


@router.get(
    "/{dataset_id}/versions",
    response_model=DatasetVersionCollectionResponse,
    responses={**_COMMON_RESPONSES, 400: {"model": ProblemDetails}},
)
async def list_dataset_versions(
    dataset_id: UUID,
    request: Request,
    session: CurrentSession,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> DatasetVersionCollectionResponse:
    try:
        page = await _service(request).list_versions(
            actor=session.user,
            dataset_id=dataset_id,
            cursor=cursor,
            limit=limit,
        )
    except InvalidDatasetCursorError as exc:
        raise ApiProblem(
            status_code=400,
            code="invalid_cursor",
            title="Invalid pagination cursor",
            detail="The supplied dataset-version pagination cursor is invalid.",
        ) from exc
    except DatasetNotFoundError as exc:
        raise _not_found() from exc
    return DatasetVersionCollectionResponse(
        data=[_version_response(item) for item in page.versions],
        meta=CursorMeta(next_cursor=page.next_cursor),
    )


@router.get(
    "/{dataset_id}/versions/{version_id}",
    response_model=DatasetVersionResponse,
    responses=_COMMON_RESPONSES,
)
async def read_dataset_version(
    dataset_id: UUID,
    version_id: UUID,
    request: Request,
    session: CurrentSession,
) -> DatasetVersionResponse:
    try:
        version = await _service(request).get_version(
            actor=session.user,
            dataset_id=dataset_id,
            version_id=version_id,
        )
    except DatasetNotFoundError as exc:
        raise _not_found() from exc
    return _version_response(version)


@router.patch(
    "/{dataset_id}",
    response_model=DatasetResponse,
    responses={**_COMMON_RESPONSES, 409: {"model": ProblemDetails}},
)
async def rename_dataset(
    dataset_id: UUID,
    payload: UpdateDatasetRequest,
    request: Request,
    session: CsrfSession,
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
    session: CurrentSession,
) -> DatasetDraftResponse:
    try:
        draft = await _service(request).get_draft(actor=session.user, dataset_id=dataset_id)
    except DatasetNotFoundError as exc:
        raise _not_found() from exc
    response.headers["ETag"] = draft_etag(draft.revision)
    return _draft_response(draft)


@router.post(
    "/{dataset_id}/draft/impact-analyses",
    response_model=DatasetImpactResponse,
    responses={
        **_COMMON_RESPONSES,
        200: {"headers": _ETAG_HEADER},
        400: {"model": ProblemDetails},
        409: {"model": ProblemDetails},
        428: {"model": ProblemDetails},
    },
)
async def analyze_dataset_draft_impact(
    dataset_id: UUID,
    request: Request,
    response: Response,
    session: CsrfSession,
    if_match: Annotated[str | None, Header(alias="If-Match", max_length=64)] = None,
) -> DatasetImpactResponse:
    try:
        impact = await _service(request).analyze_draft_impact(
            actor=session.user,
            dataset_id=dataset_id,
            expected_revision=expected_draft_revision(if_match),
            request_id=request_id(),
        )
    except DatasetRevisionConflictError as exc:
        raise ApiProblem(
            status_code=409,
            code="dataset_revision_conflict",
            title="Dataset draft changed",
            detail="Refetch and validate the latest draft before analyzing impact.",
        ) from exc
    except DatasetDraftImpactNotValidatedError as exc:
        raise ApiProblem(
            status_code=409,
            code="dataset_draft_not_validated",
            title="Dataset draft is not validated",
            detail="Validate the current draft before analyzing its schema impact.",
        ) from exc
    except DatasetNotFoundError as exc:
        raise _not_found() from exc
    response.headers["ETag"] = draft_etag(impact.draft_revision)
    return _impact_response(impact)


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
    session: CsrfSession,
    if_match: Annotated[str | None, Header(alias="If-Match", max_length=64)] = None,
) -> DatasetDraftResponse:
    try:
        draft = await _service(request).replace_draft(
            actor=session.user,
            dataset_id=dataset_id,
            expected_revision=expected_draft_revision(if_match),
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
    response.headers["ETag"] = draft_etag(draft.revision)
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
    session: CsrfSession,
    payload: ValidateDatasetDraftRequest | None = None,
    if_match: Annotated[str | None, Header(alias="If-Match", max_length=64)] = None,
) -> DatasetValidationResponse:
    try:
        draft = await _validation_service(request).validate(
            actor=session.user,
            dataset_id=dataset_id,
            expected_revision=expected_draft_revision(if_match),
            request_id=request_id(),
            override_reason=payload.override_reason if payload is not None else None,
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
    except QuerySafetyAdministratorRequiredError as exc:
        raise ApiProblem(
            status_code=403,
            code="query_safety_override_forbidden",
            title="Query Safety override forbidden",
            detail="Only an administrator may override a blocked validation.",
        ) from exc
    except QuerySafetyOverrideNotApplicableError as exc:
        raise ApiProblem(
            status_code=409,
            code="query_safety_override_not_applicable",
            title="Query Safety override not applicable",
            detail="The fresh validation did not contain a blocking policy finding.",
        ) from exc
    except ValueError as exc:
        raise ApiProblem(
            status_code=422,
            code="query_safety_override_invalid",
            title="Query Safety override invalid",
            detail=str(exc),
        ) from exc
    if draft.validation is None:  # pragma: no cover - persistence contract guard
        raise RuntimeError("Dataset validation was not persisted")
    return _validation_response(draft.validation)


@router.post(
    "/{dataset_id}/draft/previews",
    response_model=DatasetPreviewResponse,
    responses={
        **_COMMON_RESPONSES,
        400: {"model": ProblemDetails},
        409: {"model": ProblemDetails},
        428: {"model": ProblemDetails},
        502: {"model": ProblemDetails},
    },
)
async def preview_dataset_draft(
    dataset_id: UUID,
    payload: PreviewDatasetDraftRequest,
    request: Request,
    response: Response,
    session: CsrfSession,
    if_match: Annotated[str | None, Header(alias="If-Match", max_length=64)] = None,
) -> DatasetPreviewResponse:
    try:
        preview = await _preview_service(request).preview(
            actor=session.user,
            dataset_id=dataset_id,
            expected_revision=expected_draft_revision(if_match),
            limit=payload.limit,
            request_id=request_id(),
        )
    except DatasetRevisionConflictError as exc:
        raise ApiProblem(
            status_code=409,
            code="dataset_revision_conflict",
            title="Dataset draft changed",
            detail="Refetch and validate the latest draft before previewing it.",
        ) from exc
    except DatasetDraftNotValidatedError as exc:
        raise ApiProblem(
            status_code=409,
            code="dataset_draft_not_validated",
            title="Dataset draft is not validated",
            detail="Validate the current draft successfully before requesting a preview.",
        ) from exc
    except DatasetNotFoundError as exc:
        raise _not_found() from exc
    except SourceDatasetPreviewUnavailableError as exc:
        raise ApiProblem(
            status_code=502,
            code="source_preview_unavailable",
            title="Source preview unavailable",
            detail="The PostgreSQL preview could not be completed safely.",
        ) from exc
    except PreviewPolicyLimitExceededError as exc:
        raise ApiProblem(
            status_code=422,
            code="preview_policy_limit_exceeded",
            title="Preview policy limit exceeded",
            detail="The requested row limit exceeds the current preview policy.",
        ) from exc
    response.headers["ETag"] = draft_etag(preview.revision)
    return _preview_response(preview)
