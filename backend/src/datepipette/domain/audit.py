"""Audit-event vocabulary."""

from enum import StrEnum


class AuditActorType(StrEnum):
    USER = "user"
    ANONYMOUS = "anonymous"
    SYSTEM = "system"


class AuditOutcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    THROTTLED = "throttled"
