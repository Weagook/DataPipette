"""Connection visibility scopes and effective source access."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from datepipette.domain.access_control import GrantPrincipalType


class ConnectionScopeType(StrEnum):
    ALL = "all"
    SCHEMA = "schema"
    TABLE = "table"


@dataclass(frozen=True, slots=True)
class ConnectionScope:
    type: ConnectionScopeType
    schema_name: str | None = None
    relation_name: str | None = None


@dataclass(frozen=True, slots=True)
class ConnectionGrant:
    id: UUID
    connection_id: UUID
    principal_type: GrantPrincipalType
    principal_id: UUID
    scope: ConnectionScope
    created_by: UUID
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ConnectionAccess:
    connection_id: UUID
    all_access: bool
    schemas: frozenset[str]
    tables: frozenset[tuple[str, str]]

    def allows_schema(self, schema_name: str) -> bool:
        return (
            self.all_access
            or schema_name in self.schemas
            or any(schema == schema_name for schema, _relation in self.tables)
        )

    def allows_relation(self, schema_name: str, relation_name: str) -> bool:
        return (
            self.all_access
            or schema_name in self.schemas
            or (schema_name, relation_name) in self.tables
        )

    @property
    def is_empty(self) -> bool:
        return not self.all_access and not self.schemas and not self.tables
