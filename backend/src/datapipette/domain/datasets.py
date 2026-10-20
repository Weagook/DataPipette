"""Dataset draft vocabulary and invariants."""

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from math import isfinite
from typing import Any
from uuid import UUID

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError
from sqlglot.optimizer.scope import traverse_scope

_OUTPUT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MAX_DATASET_FIELDS = 500
MAX_DATASET_RELATIONS = 10
MAX_JOIN_CONDITIONS = 8
MAX_CANVAS_NODES = 24
MAX_SQL_STATEMENT_BYTES = 65_536


class DatasetVersionState(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    SUPERSEDED = "superseded"


class DatasetDefinitionKind(StrEnum):
    VISUAL = "visual"
    SQL = "sql"


class DatasetValidationState(StrEnum):
    VALID = "valid"
    VALID_WITH_WARNINGS = "valid_with_warnings"
    BLOCKED = "blocked"
    INVALID = "invalid"


class ValidationSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    BLOCKED = "blocked"
    ERROR = "error"


class ValidationFindingCode(StrEnum):
    SOURCE_RELATION_NOT_FOUND = "source_relation_not_found"
    SOURCE_FIELD_NOT_FOUND = "source_field_not_found"
    SOURCE_JOIN_COLUMN_NOT_FOUND = "source_join_column_not_found"
    LARGE_ESTIMATED_RESULT = "large_estimated_result"
    HIGH_PLANNER_COST = "high_planner_cost"
    TOO_MANY_JOINS = "too_many_joins"
    LARGE_SEQUENTIAL_SCAN = "large_sequential_scan"
    NO_VISIBLE_INDEX_FOR_LARGE_SCAN = "no_visible_index_for_large_scan"
    LARGE_SORT = "large_sort"
    STALE_RELATION_STATISTICS = "stale_relation_statistics"
    SQL_SOURCE_RELATION_NOT_FOUND = "sql_source_relation_not_found"
    SQL_QUERY_INVALID = "sql_query_invalid"
    SQL_RESULT_SCHEMA_MISMATCH = "sql_result_schema_mismatch"


class DatasetRecommendationCode(StrEnum):
    REDUCE_RESULT_SIZE = "reduce_result_size"
    REVIEW_QUERY_COST = "review_query_cost"
    REDUCE_JOIN_COMPLEXITY = "reduce_join_complexity"
    REVIEW_SEQUENTIAL_SCAN = "review_sequential_scan"
    CONSIDER_JOIN_INDEX = "consider_join_index"
    REVIEW_SORT = "review_sort"
    REFRESH_STATISTICS = "refresh_statistics"


class PreviewTruncationReason(StrEnum):
    ROW_LIMIT = "row_limit"
    BYTE_LIMIT = "byte_limit"


class VisualJoinType(StrEnum):
    INNER = "inner"
    LEFT = "left"


class FieldDisplayFormat(StrEnum):
    DATE_DD_MM_YYYY = "date_dd_mm_yyyy"
    DATE_YYYY_MM_DD = "date_yyyy_mm_dd"
    DATETIME_DD_MM_YYYY_HH_MM = "datetime_dd_mm_yyyy_hh_mm"
    NUMBER_INTEGER = "number_integer"
    NUMBER_DECIMAL_2 = "number_decimal_2"
    PERCENT = "percent"


def _required_text(value: str, label: str, maximum: int) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} must not be empty")
    if len(normalized) > maximum:
        raise ValueError(f"{label} must contain at most {maximum} characters")
    return normalized


def _output_name(value: str, label: str) -> str:
    normalized = _required_text(value, label, 100)
    if _OUTPUT_NAME.fullmatch(normalized) is None:
        raise ValueError(f"{label} must be an ASCII identifier")
    return normalized


@dataclass(frozen=True, slots=True)
class VisualRelation:
    id: UUID
    schema_name: str
    relation_name: str
    alias: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_name", _required_text(self.schema_name, "Schema", 255))
        object.__setattr__(
            self,
            "relation_name",
            _required_text(self.relation_name, "Relation", 255),
        )
        object.__setattr__(self, "alias", _output_name(self.alias, "Relation alias"))


@dataclass(frozen=True, slots=True)
class FieldSource:
    relation_id: UUID
    column: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "column", _required_text(self.column, "Source column", 255))


@dataclass(frozen=True, slots=True)
class FieldDisplay:
    label: str
    format: FieldDisplayFormat | None = None
    visible: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "label", _required_text(self.label, "Field label", 100))


@dataclass(frozen=True, slots=True)
class VisualField:
    id: UUID
    source: FieldSource
    name: str
    display: FieldDisplay

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _output_name(self.name, "Output field name"))


@dataclass(frozen=True, slots=True)
class VisualJoinCondition:
    left_column: str
    right_column: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "left_column",
            _required_text(self.left_column, "Left join column", 255),
        )
        object.__setattr__(
            self,
            "right_column",
            _required_text(self.right_column, "Right join column", 255),
        )


@dataclass(frozen=True, slots=True)
class VisualJoin:
    id: UUID
    type: VisualJoinType
    left_relation_id: UUID
    right_relation_id: UUID
    conditions: tuple[VisualJoinCondition, ...]

    def __post_init__(self) -> None:
        if not self.conditions:
            raise ValueError("At least one join condition is required")
        if len(self.conditions) > MAX_JOIN_CONDITIONS:
            raise ValueError(f"At most {MAX_JOIN_CONDITIONS} join conditions are allowed")
        pairs = [(condition.left_column, condition.right_column) for condition in self.conditions]
        if len(pairs) != len(set(pairs)):
            raise ValueError("Join conditions must be unique")


@dataclass(frozen=True, slots=True)
class VisualCanvasNode:
    id: str
    x: float
    y: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _required_text(self.id, "Canvas node ID", 100))
        if not isfinite(self.x) or not isfinite(self.y):
            raise ValueError("Canvas node coordinates must be finite")
        if abs(self.x) > 100_000 or abs(self.y) > 100_000:
            raise ValueError("Canvas node coordinates are outside the supported range")


@dataclass(frozen=True, slots=True)
class VisualCanvasViewport:
    x: float = 0
    y: float = 0
    zoom: float = 1

    def __post_init__(self) -> None:
        if not all(isfinite(value) for value in (self.x, self.y, self.zoom)):
            raise ValueError("Canvas viewport values must be finite")
        if abs(self.x) > 100_000 or abs(self.y) > 100_000:
            raise ValueError("Canvas viewport is outside the supported range")
        if not 0.1 <= self.zoom <= 4:
            raise ValueError("Canvas zoom must be between 0.1 and 4")


@dataclass(frozen=True, slots=True)
class VisualCanvasLayout:
    nodes: tuple[VisualCanvasNode, ...] = ()
    viewport: VisualCanvasViewport = VisualCanvasViewport()

    def __post_init__(self) -> None:
        if len(self.nodes) > MAX_CANVAS_NODES:
            raise ValueError(f"At most {MAX_CANVAS_NODES} canvas nodes are allowed")
        node_ids = [node.id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("Canvas node identifiers must be unique")


@dataclass(frozen=True, slots=True)
class VisualDatasetDefinition:
    relations: tuple[VisualRelation, ...]
    fields: tuple[VisualField, ...]
    joins: tuple[VisualJoin, ...] = ()
    canvas: VisualCanvasLayout | None = None
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version not in {1, 2}:
            raise ValueError("Unsupported visual definition schema version")
        if not self.relations:
            raise ValueError("At least one source relation is required")
        if len(self.relations) > MAX_DATASET_RELATIONS:
            raise ValueError(f"At most {MAX_DATASET_RELATIONS} source relations are allowed")
        if self.schema_version == 1 and (len(self.relations) != 1 or self.joins):
            raise ValueError("Schema version 1 requires one relation and no joins")
        if self.schema_version == 1 and self.canvas is not None:
            raise ValueError("Schema version 1 does not support canvas metadata")
        if not self.fields:
            raise ValueError("At least one field is required")
        if len(self.fields) > MAX_DATASET_FIELDS:
            raise ValueError(f"At most {MAX_DATASET_FIELDS} fields are allowed")
        field_ids = [field.id for field in self.fields]
        if len(field_ids) != len(set(field_ids)):
            raise ValueError("Field identifiers must be unique")
        output_names = [field.name.casefold() for field in self.fields]
        if len(output_names) != len(set(output_names)):
            raise ValueError("Output field names must be unique")
        relation_ids = [relation.id for relation in self.relations]
        if len(relation_ids) != len(set(relation_ids)):
            raise ValueError("Relation identifiers must be unique")
        aliases = [relation.alias.casefold() for relation in self.relations]
        if len(aliases) != len(set(aliases)):
            raise ValueError("Relation aliases must be unique")
        known_relations = set(relation_ids)
        if any(field.source.relation_id not in known_relations for field in self.fields):
            raise ValueError("Every field must reference a source relation")
        join_ids = [join.id for join in self.joins]
        if len(join_ids) != len(set(join_ids)):
            raise ValueError("Join identifiers must be unique")
        if len(self.joins) != len(self.relations) - 1:
            raise ValueError("Every additional relation requires exactly one join")
        connected = {self.relations[0].id}
        for join in self.joins:
            if join.left_relation_id not in connected:
                raise ValueError("A join left relation must already be connected")
            if join.right_relation_id not in known_relations:
                raise ValueError("A join right relation must reference a source relation")
            if join.right_relation_id in connected:
                raise ValueError("A join right relation must be new")
            if join.left_relation_id == join.right_relation_id:
                raise ValueError("A join cannot connect a relation to itself")
            connected.add(join.right_relation_id)
        if connected != known_relations:
            raise ValueError("Every source relation must be connected")

    @property
    def relation(self) -> VisualRelation:
        """Return the root relation for compatibility with single-relation callers."""

        return self.relations[0]

    def to_document(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "kind": DatasetDefinitionKind.VISUAL.value,
            "schema_version": self.schema_version,
            "source": {
                "relations": [
                    {
                        "id": str(relation.id),
                        "schema": relation.schema_name,
                        "name": relation.relation_name,
                        "alias": relation.alias,
                    }
                    for relation in self.relations
                ]
            },
            "fields": [
                {
                    "id": str(field.id),
                    "source": {
                        "relation_id": str(field.source.relation_id),
                        "column": field.source.column,
                    },
                    "name": field.name,
                    "display": {
                        "label": field.display.label,
                        "format": field.display.format.value if field.display.format else None,
                        "visible": field.display.visible,
                    },
                }
                for field in self.fields
            ],
            "joins": [
                {
                    "id": str(join.id),
                    "type": join.type.value,
                    "left_relation_id": str(join.left_relation_id),
                    "right_relation_id": str(join.right_relation_id),
                    "conditions": [
                        {
                            "left_column": condition.left_column,
                            "right_column": condition.right_column,
                        }
                        for condition in join.conditions
                    ],
                }
                for join in self.joins
            ],
        }
        if self.canvas is not None:
            document["canvas"] = {
                "nodes": [{"id": node.id, "x": node.x, "y": node.y} for node in self.canvas.nodes],
                "viewport": {
                    "x": self.canvas.viewport.x,
                    "y": self.canvas.viewport.y,
                    "zoom": self.canvas.viewport.zoom,
                },
            }
        return document

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> "VisualDatasetDefinition":
        try:
            relations = document["source"]["relations"]
            joins = document["joins"]
            parsed_relations = tuple(
                VisualRelation(
                    id=UUID(relation_value["id"]),
                    schema_name=relation_value["schema"],
                    relation_name=relation_value["name"],
                    alias=relation_value["alias"],
                )
                for relation_value in relations
            )
            fields = tuple(
                VisualField(
                    id=UUID(value["id"]),
                    source=FieldSource(
                        relation_id=UUID(value["source"]["relation_id"]),
                        column=value["source"]["column"],
                    ),
                    name=value["name"],
                    display=FieldDisplay(
                        label=value["display"]["label"],
                        format=(
                            FieldDisplayFormat(value["display"]["format"])
                            if value["display"]["format"] is not None
                            else None
                        ),
                        visible=value["display"]["visible"],
                    ),
                )
                for value in document["fields"]
            )
            canvas_value = document.get("canvas")
            canvas = (
                VisualCanvasLayout(
                    nodes=tuple(
                        VisualCanvasNode(
                            id=value["id"],
                            x=value["x"],
                            y=value["y"],
                        )
                        for value in canvas_value["nodes"]
                    ),
                    viewport=VisualCanvasViewport(
                        x=canvas_value["viewport"]["x"],
                        y=canvas_value["viewport"]["y"],
                        zoom=canvas_value["viewport"]["zoom"],
                    ),
                )
                if canvas_value is not None
                else None
            )
            return cls(
                schema_version=document["schema_version"],
                relations=parsed_relations,
                fields=fields,
                canvas=canvas,
                joins=tuple(
                    VisualJoin(
                        id=UUID(value["id"]),
                        type=VisualJoinType(value["type"]),
                        left_relation_id=UUID(value["left_relation_id"]),
                        right_relation_id=UUID(value["right_relation_id"]),
                        conditions=tuple(
                            VisualJoinCondition(
                                left_column=condition["left_column"],
                                right_column=condition["right_column"],
                            )
                            for condition in value["conditions"]
                        ),
                    )
                    for value in joins
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Stored visual definition is invalid") from exc


@dataclass(frozen=True, slots=True)
class SqlSourceRelation:
    schema_name: str
    relation_name: str


def _inspect_sql(statement: str) -> tuple[str, tuple[SqlSourceRelation, ...]]:
    normalized_input = statement.strip()
    if not normalized_input:
        raise ValueError("SQL statement must not be empty")
    if len(normalized_input.encode("utf-8")) > MAX_SQL_STATEMENT_BYTES:
        raise ValueError("SQL statement exceeds the maximum serialized size")
    try:
        parsed = sqlglot.parse(normalized_input, read="postgres")
    except ParseError as exc:
        raise ValueError("SQL statement is not valid PostgreSQL syntax") from exc
    if len(parsed) != 1 or not isinstance(parsed[0], exp.Query):
        raise ValueError("SQL definition must contain exactly one SELECT statement")
    root = parsed[0]
    if any(not isinstance(cte.this, exp.Query) for cte in root.find_all(exp.CTE)):
        raise ValueError("SQL statement must be read-only and cannot modify data")
    if root.find(exp.Into) is not None or root.find(exp.Lock) is not None:
        raise ValueError("SQL statement must be read-only and cannot lock rows")
    if root.find(exp.Parameter) is not None or root.find(exp.Placeholder) is not None:
        raise ValueError("SQL dataset statements cannot contain external parameters")
    relations: set[SqlSourceRelation] = set()
    for scope in traverse_scope(root):
        for source in scope.sources.values():
            if not isinstance(source, exp.Table):
                continue
            if source.catalog or not source.db:
                raise ValueError("Every SQL source relation must be schema-qualified")
            relations.add(SqlSourceRelation(source.db, source.name))
    return (
        root.sql(dialect="postgres", pretty=False),
        tuple(sorted(relations, key=lambda item: (item.schema_name, item.relation_name))),
    )


@dataclass(frozen=True, slots=True)
class SqlField:
    id: UUID
    name: str
    display: FieldDisplay

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _output_name(self.name, "Output field name"))


@dataclass(frozen=True, slots=True)
class SqlDatasetDefinition:
    statement: str
    fields: tuple[SqlField, ...]
    schema_version: int = 1
    execution_statement: str = field(init=False, repr=False, compare=False)
    source_relations: tuple[SqlSourceRelation, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("Unsupported SQL definition schema version")
        if not self.fields:
            raise ValueError("At least one SQL output field is required")
        if len(self.fields) > MAX_DATASET_FIELDS:
            raise ValueError(f"At most {MAX_DATASET_FIELDS} fields are allowed")
        field_ids = [item.id for item in self.fields]
        if len(field_ids) != len(set(field_ids)):
            raise ValueError("Field identifiers must be unique")
        output_names = [item.name.casefold() for item in self.fields]
        if len(output_names) != len(set(output_names)):
            raise ValueError("Output field names must be unique")
        execution_statement, relations = _inspect_sql(self.statement)
        object.__setattr__(self, "statement", self.statement.strip())
        object.__setattr__(self, "execution_statement", execution_statement)
        object.__setattr__(self, "source_relations", relations)

    def to_document(self) -> dict[str, Any]:
        return {
            "kind": DatasetDefinitionKind.SQL.value,
            "schema_version": self.schema_version,
            "statement": self.statement,
            "fields": [
                {
                    "id": str(item.id),
                    "name": item.name,
                    "display": {
                        "label": item.display.label,
                        "format": item.display.format.value if item.display.format else None,
                        "visible": item.display.visible,
                    },
                }
                for item in self.fields
            ],
        }

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> "SqlDatasetDefinition":
        try:
            return cls(
                schema_version=document["schema_version"],
                statement=document["statement"],
                fields=tuple(
                    SqlField(
                        id=UUID(value["id"]),
                        name=value["name"],
                        display=FieldDisplay(
                            label=value["display"]["label"],
                            format=(
                                FieldDisplayFormat(value["display"]["format"])
                                if value["display"]["format"] is not None
                                else None
                            ),
                            visible=value["display"]["visible"],
                        ),
                    )
                    for value in document["fields"]
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Stored SQL definition is invalid") from exc


DatasetDefinition = VisualDatasetDefinition | SqlDatasetDefinition
DatasetField = VisualField | SqlField


def dataset_definition_from_document(document: dict[str, Any]) -> DatasetDefinition:
    kind = document.get("kind", DatasetDefinitionKind.VISUAL.value)
    if kind == DatasetDefinitionKind.VISUAL.value:
        return VisualDatasetDefinition.from_document(document)
    if kind == DatasetDefinitionKind.SQL.value:
        return SqlDatasetDefinition.from_document(document)
    raise ValueError("Stored dataset definition kind is invalid")


def definition_relations(definition: DatasetDefinition) -> tuple[tuple[str, str], ...]:
    if isinstance(definition, VisualDatasetDefinition):
        return tuple(
            (relation.schema_name, relation.relation_name) for relation in definition.relations
        )
    return tuple(
        (relation.schema_name, relation.relation_name) for relation in definition.source_relations
    )


@dataclass(frozen=True, slots=True)
class Dataset:
    id: UUID
    name: str
    owner_id: UUID
    connection_id: UUID
    active_version_id: UUID | None
    active_snapshot_id: UUID | None
    draft_revision: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class DatasetValidationFinding:
    code: ValidationFindingCode
    severity: ValidationSeverity
    field_id: UUID | None = None
    relation_id: UUID | None = None
    join_id: UUID | None = None
    observed_value: float | None = None
    threshold_value: float | None = None


@dataclass(frozen=True, slots=True)
class DatasetRecommendation:
    code: DatasetRecommendationCode
    finding_code: ValidationFindingCode
    relation_id: UUID | None = None
    join_id: UUID | None = None
    column_names: tuple[str, ...] = ()


def build_dataset_recommendations(
    definition: DatasetDefinition,
    findings: tuple[DatasetValidationFinding, ...],
) -> tuple[DatasetRecommendation, ...]:
    """Build deterministic review advice without generating executable source DDL."""

    recommendations: list[DatasetRecommendation] = []
    global_codes = {
        ValidationFindingCode.LARGE_ESTIMATED_RESULT: DatasetRecommendationCode.REDUCE_RESULT_SIZE,
        ValidationFindingCode.HIGH_PLANNER_COST: DatasetRecommendationCode.REVIEW_QUERY_COST,
        ValidationFindingCode.TOO_MANY_JOINS: DatasetRecommendationCode.REDUCE_JOIN_COMPLEXITY,
        ValidationFindingCode.LARGE_SORT: DatasetRecommendationCode.REVIEW_SORT,
    }
    seen_global: set[ValidationFindingCode] = set()
    for finding in findings:
        recommendation_code = global_codes.get(finding.code)
        if recommendation_code is not None and finding.code not in seen_global:
            recommendations.append(
                DatasetRecommendation(
                    code=recommendation_code,
                    finding_code=finding.code,
                )
            )
            seen_global.add(finding.code)
        if finding.code is ValidationFindingCode.LARGE_SEQUENTIAL_SCAN:
            recommendations.append(
                DatasetRecommendation(
                    code=DatasetRecommendationCode.REVIEW_SEQUENTIAL_SCAN,
                    finding_code=finding.code,
                    relation_id=finding.relation_id,
                )
            )
            if isinstance(definition, VisualDatasetDefinition) and finding.relation_id is not None:
                for join in definition.joins:
                    columns: tuple[str, ...] = ()
                    if join.left_relation_id == finding.relation_id:
                        columns = tuple(condition.left_column for condition in join.conditions)
                    elif join.right_relation_id == finding.relation_id:
                        columns = tuple(condition.right_column for condition in join.conditions)
                    if columns:
                        recommendations.append(
                            DatasetRecommendation(
                                code=DatasetRecommendationCode.CONSIDER_JOIN_INDEX,
                                finding_code=finding.code,
                                relation_id=finding.relation_id,
                                join_id=join.id,
                                column_names=columns,
                            )
                        )
        elif finding.code is ValidationFindingCode.STALE_RELATION_STATISTICS:
            recommendations.append(
                DatasetRecommendation(
                    code=DatasetRecommendationCode.REFRESH_STATISTICS,
                    finding_code=finding.code,
                    relation_id=finding.relation_id,
                )
            )
    return tuple(recommendations)


@dataclass(frozen=True, slots=True)
class ValidatedDatasetField:
    field_id: UUID
    name: str
    data_type: str
    nullable: bool


@dataclass(frozen=True, slots=True)
class DatasetPlanSummary:
    node_type: str
    estimated_rows: int
    startup_cost: float
    total_cost: float
    node_count: int = 1
    join_count: int = 0
    sort_count: int = 0
    max_plan_rows: int = 0
    max_sequential_scan_rows: int = 0
    max_sort_rows: int = 0


@dataclass(frozen=True, slots=True)
class DatasetRelationStatistics:
    relation_id: UUID | None
    schema_name: str
    relation_name: str
    approximate_rows: int
    relation_bytes: int
    index_bytes: int
    visible_index_count: int
    statistics_updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class QuerySafetyPolicy:
    estimated_rows_warning: int = 1_000_000
    estimated_rows_block: int = 10_000_000
    total_cost_warning: float = 100_000.0
    total_cost_block: float = 1_000_000.0
    join_count_warning: int = 5
    join_count_block: int = 9
    sequential_scan_rows_warning: int = 250_000
    sort_rows_warning: int = 500_000
    stale_statistics_days: int = 30
    preview_statement_timeout_seconds: int = 5
    preview_max_rows: int = 200
    preview_max_response_bytes: int = 1_048_576
    refresh_statement_timeout_seconds: int = 1800
    refresh_max_rows: int = 10_000_000
    interactive_query_timeout_seconds: float = 10.0
    interactive_query_memory_limit_mb: int = 256
    interactive_query_threads: int = 2
    interactive_query_concurrency: int = 4
    interactive_query_temp_limit_mb: int = 512
    interactive_query_max_response_bytes: int = 2_097_152
    export_timeout_seconds: int = 1800
    export_memory_limit_mb: int = 512
    export_threads: int = 2
    export_temp_limit_mb: int = 2048
    export_max_rows: int = 5_000_000
    export_max_bytes: int = 1_073_741_824
    background_job_concurrency: int = 2

    def __post_init__(self) -> None:
        numeric_values = (
            self.estimated_rows_warning,
            self.estimated_rows_block,
            self.total_cost_warning,
            self.total_cost_block,
            self.join_count_warning,
            self.join_count_block,
            self.sequential_scan_rows_warning,
            self.sort_rows_warning,
            self.stale_statistics_days,
            self.preview_statement_timeout_seconds,
            self.preview_max_rows,
            self.preview_max_response_bytes,
            self.refresh_statement_timeout_seconds,
            self.refresh_max_rows,
            self.interactive_query_timeout_seconds,
            self.interactive_query_memory_limit_mb,
            self.interactive_query_threads,
            self.interactive_query_concurrency,
            self.interactive_query_temp_limit_mb,
            self.interactive_query_max_response_bytes,
            self.export_timeout_seconds,
            self.export_memory_limit_mb,
            self.export_threads,
            self.export_temp_limit_mb,
            self.export_max_rows,
            self.export_max_bytes,
            self.background_job_concurrency,
        )
        if any(value <= 0 for value in numeric_values):
            raise ValueError("Query Safety thresholds must be positive")
        if self.estimated_rows_warning >= self.estimated_rows_block:
            raise ValueError("Estimated row warning threshold must be below block threshold")
        if self.total_cost_warning >= self.total_cost_block:
            raise ValueError("Planner cost warning threshold must be below block threshold")
        if self.join_count_warning >= self.join_count_block:
            raise ValueError("Join warning threshold must be below block threshold")
        if self.preview_max_rows > 200:
            raise ValueError("Preview row limit cannot exceed the hard API ceiling")
        if self.preview_max_response_bytes > 1_048_576:
            raise ValueError("Preview byte limit cannot exceed the hard API ceiling")
        interactive_bounds = (
            (self.interactive_query_timeout_seconds, 60, "timeout"),
            (self.interactive_query_memory_limit_mb, 4096, "memory"),
            (self.interactive_query_threads, 16, "thread"),
            (self.interactive_query_concurrency, 64, "concurrency"),
            (self.interactive_query_temp_limit_mb, 8192, "temporary disk"),
            (self.interactive_query_max_response_bytes, 16_777_216, "response byte"),
        )
        for value, maximum, label in interactive_bounds:
            if value > maximum:
                raise ValueError(f"Interactive query {label} limit cannot exceed the hard ceiling")
        if self.interactive_query_memory_limit_mb < 64:
            raise ValueError("Interactive query memory limit must be at least 64 MiB")
        if self.interactive_query_temp_limit_mb < 64:
            raise ValueError("Interactive query temporary disk limit must be at least 64 MiB")
        if self.interactive_query_max_response_bytes < 65_536:
            raise ValueError("Interactive query response limit must be at least 64 KiB")
        export_bounds = (
            (self.export_timeout_seconds, 86_400, "timeout"),
            (self.export_memory_limit_mb, 4096, "memory"),
            (self.export_threads, 16, "thread"),
            (self.export_temp_limit_mb, 32_768, "temporary disk"),
            (self.export_max_rows, 10_000_000, "row"),
            (self.export_max_bytes, 10_737_418_240, "artifact byte"),
            (self.background_job_concurrency, 64, "background concurrency"),
        )
        for value, maximum, label in export_bounds:
            if value > maximum:
                raise ValueError(f"Export {label} limit cannot exceed the hard ceiling")
        if self.export_memory_limit_mb < 64:
            raise ValueError("Export memory limit must be at least 64 MiB")
        if self.export_temp_limit_mb < 64:
            raise ValueError("Export temporary disk limit must be at least 64 MiB")
        if self.export_max_bytes < 65_536:
            raise ValueError("Export artifact limit must be at least 64 KiB")


@dataclass(frozen=True, slots=True)
class QuerySafetyOverride:
    actor_id: UUID
    reason: str
    occurred_at: datetime
    finding_codes: tuple[ValidationFindingCode, ...]


@dataclass(frozen=True, slots=True)
class DatasetValidation:
    state: DatasetValidationState
    validated_revision: int
    validated_at: datetime
    compiled_sql: str | None
    result_schema: tuple[ValidatedDatasetField, ...]
    plan: DatasetPlanSummary | None
    findings: tuple[DatasetValidationFinding, ...]
    relation_statistics: tuple[DatasetRelationStatistics, ...] = ()
    policy_revision: int = 0
    query_safety_override: QuerySafetyOverride | None = None
    recommendations: tuple[DatasetRecommendation, ...] = ()

    def to_document(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "validated_revision": self.validated_revision,
            "validated_at": self.validated_at.isoformat(),
            "compiled_sql": self.compiled_sql,
            "result_schema": [
                {
                    "field_id": str(field.field_id),
                    "name": field.name,
                    "data_type": field.data_type,
                    "nullable": field.nullable,
                }
                for field in self.result_schema
            ],
            "plan": (
                {
                    "node_type": self.plan.node_type,
                    "estimated_rows": self.plan.estimated_rows,
                    "startup_cost": self.plan.startup_cost,
                    "total_cost": self.plan.total_cost,
                    "node_count": self.plan.node_count,
                    "join_count": self.plan.join_count,
                    "sort_count": self.plan.sort_count,
                    "max_plan_rows": self.plan.max_plan_rows,
                    "max_sequential_scan_rows": self.plan.max_sequential_scan_rows,
                    "max_sort_rows": self.plan.max_sort_rows,
                }
                if self.plan is not None
                else None
            ),
            "findings": [
                {
                    "code": finding.code.value,
                    "severity": finding.severity.value,
                    "field_id": str(finding.field_id) if finding.field_id else None,
                    "relation_id": str(finding.relation_id) if finding.relation_id else None,
                    "join_id": str(finding.join_id) if finding.join_id else None,
                    "observed_value": finding.observed_value,
                    "threshold_value": finding.threshold_value,
                }
                for finding in self.findings
            ],
            "relation_statistics": [
                {
                    "relation_id": str(item.relation_id) if item.relation_id else None,
                    "schema_name": item.schema_name,
                    "relation_name": item.relation_name,
                    "approximate_rows": item.approximate_rows,
                    "relation_bytes": item.relation_bytes,
                    "index_bytes": item.index_bytes,
                    "visible_index_count": item.visible_index_count,
                    "statistics_updated_at": (
                        item.statistics_updated_at.isoformat()
                        if item.statistics_updated_at is not None
                        else None
                    ),
                }
                for item in self.relation_statistics
            ],
            "recommendations": [
                {
                    "code": item.code.value,
                    "finding_code": item.finding_code.value,
                    "relation_id": str(item.relation_id) if item.relation_id else None,
                    "join_id": str(item.join_id) if item.join_id else None,
                    "column_names": list(item.column_names),
                }
                for item in self.recommendations
            ],
            "policy_revision": self.policy_revision,
            "query_safety_override": (
                {
                    "actor_id": str(self.query_safety_override.actor_id),
                    "reason": self.query_safety_override.reason,
                    "occurred_at": self.query_safety_override.occurred_at.isoformat(),
                    "finding_codes": [
                        code.value for code in self.query_safety_override.finding_codes
                    ],
                }
                if self.query_safety_override is not None
                else None
            ),
        }

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> "DatasetValidation":
        try:
            plan_value = document["plan"]
            override_value = document.get("query_safety_override")
            return cls(
                state=DatasetValidationState(document["state"]),
                validated_revision=int(document["validated_revision"]),
                validated_at=datetime.fromisoformat(document["validated_at"]),
                compiled_sql=document["compiled_sql"],
                result_schema=tuple(
                    ValidatedDatasetField(
                        field_id=UUID(field["field_id"]),
                        name=field["name"],
                        data_type=field["data_type"],
                        nullable=field["nullable"],
                    )
                    for field in document["result_schema"]
                ),
                plan=(
                    DatasetPlanSummary(
                        node_type=plan_value["node_type"],
                        estimated_rows=int(plan_value["estimated_rows"]),
                        startup_cost=float(plan_value["startup_cost"]),
                        total_cost=float(plan_value["total_cost"]),
                        node_count=int(plan_value.get("node_count", 1)),
                        join_count=int(plan_value.get("join_count", 0)),
                        sort_count=int(plan_value.get("sort_count", 0)),
                        max_plan_rows=int(
                            plan_value.get("max_plan_rows", plan_value["estimated_rows"])
                        ),
                        max_sequential_scan_rows=int(plan_value.get("max_sequential_scan_rows", 0)),
                        max_sort_rows=int(plan_value.get("max_sort_rows", 0)),
                    )
                    if plan_value is not None
                    else None
                ),
                findings=tuple(
                    DatasetValidationFinding(
                        code=ValidationFindingCode(finding["code"]),
                        severity=ValidationSeverity(finding["severity"]),
                        field_id=(UUID(finding["field_id"]) if finding["field_id"] else None),
                        relation_id=(
                            UUID(finding["relation_id"]) if finding.get("relation_id") else None
                        ),
                        join_id=(UUID(finding["join_id"]) if finding.get("join_id") else None),
                        observed_value=(
                            float(finding["observed_value"])
                            if finding.get("observed_value") is not None
                            else None
                        ),
                        threshold_value=(
                            float(finding["threshold_value"])
                            if finding.get("threshold_value") is not None
                            else None
                        ),
                    )
                    for finding in document["findings"]
                ),
                relation_statistics=tuple(
                    DatasetRelationStatistics(
                        relation_id=(
                            UUID(item["relation_id"]) if item.get("relation_id") else None
                        ),
                        schema_name=item["schema_name"],
                        relation_name=item["relation_name"],
                        approximate_rows=int(item["approximate_rows"]),
                        relation_bytes=int(item["relation_bytes"]),
                        index_bytes=int(item["index_bytes"]),
                        visible_index_count=int(item["visible_index_count"]),
                        statistics_updated_at=(
                            datetime.fromisoformat(item["statistics_updated_at"])
                            if item.get("statistics_updated_at")
                            else None
                        ),
                    )
                    for item in document.get("relation_statistics", ())
                ),
                recommendations=tuple(
                    DatasetRecommendation(
                        code=DatasetRecommendationCode(item["code"]),
                        finding_code=ValidationFindingCode(item["finding_code"]),
                        relation_id=(
                            UUID(item["relation_id"]) if item.get("relation_id") else None
                        ),
                        join_id=(UUID(item["join_id"]) if item.get("join_id") else None),
                        column_names=tuple(item.get("column_names", ())),
                    )
                    for item in document.get("recommendations", ())
                ),
                policy_revision=int(document.get("policy_revision", 0)),
                query_safety_override=(
                    QuerySafetyOverride(
                        actor_id=UUID(override_value["actor_id"]),
                        reason=override_value["reason"],
                        occurred_at=datetime.fromisoformat(override_value["occurred_at"]),
                        finding_codes=tuple(
                            ValidationFindingCode(code) for code in override_value["finding_codes"]
                        ),
                    )
                    if override_value is not None
                    else None
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Stored dataset validation is invalid") from exc


@dataclass(frozen=True, slots=True)
class DatasetDraft:
    id: UUID
    dataset_id: UUID
    revision: int
    definition: DatasetDefinition
    validation: DatasetValidation | None
    created_by: UUID
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class DatasetVersion:
    id: UUID
    dataset_id: UUID
    state: DatasetVersionState
    revision: int
    definition: DatasetDefinition
    validation: DatasetValidation | None
    created_by: UUID
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class DatasetPreview:
    revision: int
    policy_revision: int
    columns: tuple[ValidatedDatasetField, ...]
    rows: tuple[dict[str, Any], ...]
    requested_limit: int
    returned_rows: int
    truncated: bool
    truncation_reason: PreviewTruncationReason | None
