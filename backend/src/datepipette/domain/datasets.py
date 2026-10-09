"""Visual dataset draft vocabulary and invariants."""

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

_OUTPUT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MAX_DATASET_FIELDS = 500


class DatasetVersionState(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    SUPERSEDED = "superseded"


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
    LARGE_ESTIMATED_RESULT = "large_estimated_result"


class PreviewTruncationReason(StrEnum):
    ROW_LIMIT = "row_limit"
    BYTE_LIMIT = "byte_limit"


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
class VisualDatasetDefinition:
    relation: VisualRelation
    fields: tuple[VisualField, ...]
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("Unsupported visual definition schema version")
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
        if any(field.source.relation_id != self.relation.id for field in self.fields):
            raise ValueError("Every field must reference the single source relation")

    def to_document(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": {
                "relations": [
                    {
                        "id": str(self.relation.id),
                        "schema": self.relation.schema_name,
                        "name": self.relation.relation_name,
                        "alias": self.relation.alias,
                    }
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
            "joins": [],
        }

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> "VisualDatasetDefinition":
        try:
            relations = document["source"]["relations"]
            joins = document["joins"]
            if len(relations) != 1 or joins:
                raise ValueError
            relation_value = relations[0]
            relation = VisualRelation(
                id=UUID(relation_value["id"]),
                schema_name=relation_value["schema"],
                relation_name=relation_value["name"],
                alias=relation_value["alias"],
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
                relation=relation,
                fields=fields,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Stored visual definition is invalid") from exc


@dataclass(frozen=True, slots=True)
class Dataset:
    id: UUID
    name: str
    owner_id: UUID
    connection_id: UUID
    active_version_id: UUID | None
    draft_revision: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class DatasetValidationFinding:
    code: ValidationFindingCode
    severity: ValidationSeverity
    field_id: UUID | None = None


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
    definition: VisualDatasetDefinition
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
