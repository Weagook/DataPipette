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
class DatasetDraft:
    id: UUID
    dataset_id: UUID
    revision: int
    definition: VisualDatasetDefinition
    created_by: UUID
    created_at: datetime
    updated_at: datetime
