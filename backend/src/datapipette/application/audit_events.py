"""Administrator-only audit-event queries."""

import base64
import binascii
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from datapipette.domain.audit import AuditActorType, AuditEvent, AuditOutcome
from datapipette.domain.identity import AccountStatus, SystemRole, User


class AuditAdministratorRequiredError(Exception):
    pass


class InvalidAuditCursorError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class AuditEventFilter:
    actor_type: AuditActorType | None = None
    actor_id: UUID | None = None
    action: str | None = None
    outcome: AuditOutcome | None = None
    resource_type: str | None = None
    resource_id: UUID | None = None
    request_id: str | None = None
    occurred_from: datetime | None = None
    occurred_to: datetime | None = None

    def __post_init__(self) -> None:
        for value in (self.occurred_from, self.occurred_to):
            if value is not None and value.tzinfo is None:
                raise ValueError("Audit occurrence filters must include a timezone")
        if (
            self.occurred_from is not None
            and self.occurred_to is not None
            and self.occurred_from > self.occurred_to
        ):
            raise ValueError("Audit occurrence start must not be after the end")


@dataclass(frozen=True, slots=True)
class AuditEventPage:
    events: tuple[AuditEvent, ...]
    next_cursor: str | None


class AuditEventStore(Protocol):
    async def list_events(
        self,
        *,
        filters: AuditEventFilter,
        before_occurred_at: datetime | None,
        before_id: UUID | None,
        limit: int,
    ) -> tuple[AuditEvent, ...]: ...


def _encode_cursor(event: AuditEvent) -> str:
    value = f"{event.occurred_at.isoformat()}|{event.id}".encode()
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode_cursor(cursor: str | None) -> tuple[datetime | None, UUID | None]:
    if cursor is None:
        return None, None
    try:
        padding = "=" * (-len(cursor) % 4)
        decoded = base64.b64decode(cursor + padding, altchars=b"-_", validate=True).decode()
        occurred_at_raw, event_id_raw = decoded.split("|", maxsplit=1)
        occurred_at = datetime.fromisoformat(occurred_at_raw)
        if occurred_at.tzinfo is None:
            raise ValueError
        return occurred_at, UUID(event_id_raw)
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise InvalidAuditCursorError from exc


class AuditEventService:
    def __init__(self, store: AuditEventStore) -> None:
        self._store = store

    async def list_events(
        self,
        *,
        actor: User,
        filters: AuditEventFilter,
        cursor: str | None,
        limit: int,
    ) -> AuditEventPage:
        if actor.role is not SystemRole.ADMIN or actor.status is not AccountStatus.ACTIVE:
            raise AuditAdministratorRequiredError
        before_occurred_at, before_id = _decode_cursor(cursor)
        events = await self._store.list_events(
            filters=filters,
            before_occurred_at=before_occurred_at,
            before_id=before_id,
            limit=limit + 1,
        )
        has_more = len(events) > limit
        visible = events[:limit]
        return AuditEventPage(
            visible,
            _encode_cursor(visible[-1]) if has_more else None,
        )
