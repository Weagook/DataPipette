"""Audit-event vocabulary."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID


class AuditActorType(StrEnum):
    USER = "user"
    SERVICE_ACCOUNT = "service_account"
    ANONYMOUS = "anonymous"
    SYSTEM = "system"


class AuditOutcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    THROTTLED = "throttled"


@dataclass(frozen=True, slots=True)
class AuditEvent:
    id: UUID
    occurred_at: datetime
    actor_type: AuditActorType
    actor_id: UUID | None
    action: str
    outcome: AuditOutcome
    resource_type: str | None
    resource_id: UUID | None
    request_id: str | None
    details: dict[str, Any]
