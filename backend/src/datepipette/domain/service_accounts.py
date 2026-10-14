"""Service-account and API-token vocabulary."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from datepipette.domain.identity import AccountStatus, SystemRole, User


class ApiTokenScope(StrEnum):
    DASHBOARDS_READ = "dashboards:read"


@dataclass(frozen=True, slots=True)
class ServiceAccount:
    id: UUID
    name: str
    role: SystemRole
    status: AccountStatus
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ApiToken:
    id: UUID
    service_account_id: UUID
    name: str
    scopes: frozenset[ApiTokenScope]
    expires_at: datetime | None
    revoked_at: datetime | None
    last_used_at: datetime | None
    created_by: UUID
    created_at: datetime


@dataclass(frozen=True, slots=True)
class IssuedApiToken:
    token: ApiToken
    plaintext: str


@dataclass(frozen=True, slots=True)
class AuthenticatedApiToken:
    id: UUID
    user: User
    scopes: frozenset[ApiTokenScope]
    expires_at: datetime | None
