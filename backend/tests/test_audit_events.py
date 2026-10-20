"""Administrator audit-event query policy tests."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from datapipette.application.audit_events import (
    AuditAdministratorRequiredError,
    AuditEventFilter,
    AuditEventService,
    InvalidAuditCursorError,
)
from datapipette.domain.audit import AuditActorType, AuditEvent, AuditOutcome
from datapipette.domain.identity import AccountStatus, SystemRole, User

NOW = datetime(2026, 10, 14, 12, tzinfo=UTC)


def _user(
    role: SystemRole = SystemRole.ADMIN,
    status: AccountStatus = AccountStatus.ACTIVE,
) -> User:
    return User(
        uuid4(),
        "admin@example.com",
        "Admin",
        role,
        status,
        None,
        NOW,
        NOW,
    )


def _event(offset: int) -> AuditEvent:
    return AuditEvent(
        id=uuid4(),
        occurred_at=NOW - timedelta(seconds=offset),
        actor_type=AuditActorType.USER,
        actor_id=uuid4(),
        action="identity.user_created",
        outcome=AuditOutcome.SUCCESS,
        resource_type="user",
        resource_id=uuid4(),
        request_id=f"request-{offset}",
        details={"role": "viewer"},
    )


def test_service_enforces_admin_policy_and_uses_stable_cursor() -> None:
    events = (_event(0), _event(1), _event(2))
    store = SimpleNamespace(list_events=AsyncMock(return_value=events))
    service = AuditEventService(store)

    async def exercise() -> None:
        first = await service.list_events(
            actor=_user(),
            filters=AuditEventFilter(action="identity.user_created"),
            cursor=None,
            limit=2,
        )
        assert first.events == events[:2]
        assert first.next_cursor is not None
        assert store.list_events.await_args.kwargs["limit"] == 3

        store.list_events.return_value = ()
        second = await service.list_events(
            actor=_user(),
            filters=AuditEventFilter(),
            cursor=first.next_cursor,
            limit=2,
        )
        assert second.next_cursor is None
        assert store.list_events.await_args.kwargs["before_occurred_at"] == events[1].occurred_at
        assert store.list_events.await_args.kwargs["before_id"] == events[1].id

        for actor in (
            _user(SystemRole.ANALYST),
            _user(SystemRole.ADMIN, AccountStatus.DISABLED),
        ):
            with pytest.raises(AuditAdministratorRequiredError):
                await service.list_events(
                    actor=actor,
                    filters=AuditEventFilter(),
                    cursor=None,
                    limit=2,
                )

        with pytest.raises(InvalidAuditCursorError):
            await service.list_events(
                actor=_user(),
                filters=AuditEventFilter(),
                cursor="%%%",
                limit=2,
            )

    asyncio.run(exercise())


def test_filter_requires_ordered_timezone_aware_window() -> None:
    with pytest.raises(ValueError, match="timezone"):
        AuditEventFilter(occurred_from=datetime(2026, 10, 14))
    with pytest.raises(ValueError, match="after"):
        AuditEventFilter(occurred_from=NOW, occurred_to=NOW - timedelta(seconds=1))

    assert (
        AuditEventFilter(
            occurred_from=NOW - timedelta(seconds=1),
            occurred_to=NOW,
        ).occurred_to
        == NOW
    )
