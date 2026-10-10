"""Groups and additive dataset authorization vocabulary."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class DatasetPermission(StrEnum):
    VIEW_ROWS = "view_rows"
    EXPORT = "export"
    MANAGE = "manage"


class GrantPrincipalType(StrEnum):
    USER = "user"
    GROUP = "group"


@dataclass(frozen=True, slots=True)
class Group:
    id: UUID
    name: str
    created_by: UUID
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class GroupMembership:
    group_id: UUID
    user_id: UUID
    created_by: UUID
    created_at: datetime


@dataclass(frozen=True, slots=True)
class DatasetGrant:
    id: UUID
    dataset_id: UUID
    principal_type: GrantPrincipalType
    principal_id: UUID
    permission: DatasetPermission
    created_by: UUID
    created_at: datetime


@dataclass(frozen=True, slots=True)
class DatasetAccess:
    dataset_id: UUID
    is_owner: bool
    permissions: frozenset[DatasetPermission]

    def allows(self, permission: DatasetPermission) -> bool:
        return permission in self.permissions
