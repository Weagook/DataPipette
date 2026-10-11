"""Dataset draft vocabulary and invariants."""

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
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
    INVALID = "invalid"


class ValidationSeverity(StrEnum):
    WARNING = "warning"
    ERROR = "error"


class ValidationFindingCode(StrEnum):
    SOURCE_RELATION_NOT_FOUND = "source_relation_not_found"
    SOURCE_FIELD_NOT_FOUND = "source_field_not_found"
    SOURCE_JOIN_COLUMN_NOT_FOUND = "source_join_column_not_found"
    LARGE_ESTIMATED_RESULT = "large_estimated_result"
    SQL_SOURCE_RELATION_NOT_FOUND = "sql_source_relation_not_found"
    SQL_QUERY_INVALID = "sql_query_invalid"
    SQL_RESULT_SCHEMA_MISMATCH = "sql_result_schema_mismatch"


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
class VisualDatasetDefinition:
    relations: tuple[VisualRelation, ...]
    fields: tuple[VisualField, ...]
    joins: tuple[VisualJoin, ...] = ()
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
        return {
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
            return cls(
                schema_version=document["schema_version"],
                relations=parsed_relations,
                fields=fields,
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


@dataclass(frozen=True, slots=True)
class DatasetValidation:
    state: DatasetValidationState
    validated_revision: int
    validated_at: datetime
    compiled_sql: str | None
    result_schema: tuple[ValidatedDatasetField, ...]
    plan: DatasetPlanSummary | None
    findings: tuple[DatasetValidationFinding, ...]

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
                }
                for finding in self.findings
            ],
        }

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> "DatasetValidation":
        try:
            plan_value = document["plan"]
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
                    )
                    for finding in document["findings"]
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
    columns: tuple[ValidatedDatasetField, ...]
    rows: tuple[dict[str, Any], ...]
    requested_limit: int
    returned_rows: int
    truncated: bool
    truncation_reason: PreviewTruncationReason | None
