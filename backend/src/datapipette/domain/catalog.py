"""Live PostgreSQL source catalog vocabulary."""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class RelationKind(StrEnum):
    TABLE = "table"
    PARTITIONED_TABLE = "partitioned_table"
    VIEW = "view"
    MATERIALIZED_VIEW = "materialized_view"
    FOREIGN_TABLE = "foreign_table"


@dataclass(frozen=True, slots=True)
class CatalogRelation:
    schema_name: str
    name: str
    kind: RelationKind


@dataclass(frozen=True, slots=True)
class CatalogColumn:
    name: str
    ordinal_position: int
    data_type: str
    nullable: bool


@dataclass(frozen=True, slots=True)
class CatalogForeignKey:
    name: str
    source_columns: list[str]
    target_schema: str
    target_relation: str
    target_columns: list[str]


@dataclass(frozen=True, slots=True)
class CatalogRelationDetail:
    relation: CatalogRelation
    columns: list[CatalogColumn]
    primary_key: list[str]
    foreign_keys: list[CatalogForeignKey]


@dataclass(frozen=True, slots=True)
class CatalogRelationReference:
    id: UUID
    schema_name: str
    relation_name: str


@dataclass(frozen=True, slots=True)
class CatalogJoinCondition:
    source_column: str
    target_column: str


@dataclass(frozen=True, slots=True)
class CatalogJoinSuggestion:
    foreign_key_name: str
    source_relation_id: UUID
    target_relation_id: UUID
    conditions: tuple[CatalogJoinCondition, ...]
