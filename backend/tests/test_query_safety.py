"""Managed Query Safety policy and override authorization tests."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from datepipette.application.dataset_validation import DatasetValidationService
from datepipette.application.query_safety import (
    ManagedQuerySafetyPolicy,
    QuerySafetyAdministratorRequiredError,
    QuerySafetyPolicyService,
)
from datepipette.domain.datasets import QuerySafetyPolicy
from datepipette.domain.identity import AccountStatus, SystemRole, User

NOW = datetime(2026, 10, 14, tzinfo=UTC)


def _user(role: SystemRole, status: AccountStatus = AccountStatus.ACTIVE) -> User:
    return User(
        uuid4(),
        "user@example.com",
        "User",
        role,
        status,
        None,
        NOW,
        NOW,
    )


def test_policy_service_requires_active_admin_and_preserves_revision_contract() -> None:
    managed = ManagedQuerySafetyPolicy(QuerySafetyPolicy(), 0, None, None)
    store = SimpleNamespace(
        get=AsyncMock(return_value=managed),
        replace=AsyncMock(
            return_value=ManagedQuerySafetyPolicy(QuerySafetyPolicy(), 1, NOW, uuid4())
        ),
    )
    service = QuerySafetyPolicyService(store)

    async def exercise() -> None:
        admin = _user(SystemRole.ADMIN)
        assert await service.get(actor=admin) == managed
        replaced = await service.replace(
            actor=admin,
            policy=QuerySafetyPolicy(),
            expected_revision=0,
            request_id="request-policy",
        )
        assert replaced.revision == 1
        assert store.replace.await_args.kwargs["expected_revision"] == 0
        for actor in (
            _user(SystemRole.ANALYST),
            _user(SystemRole.ADMIN, AccountStatus.DISABLED),
        ):
            with pytest.raises(QuerySafetyAdministratorRequiredError):
                await service.get(actor=actor)

    asyncio.run(exercise())


def test_block_override_rejects_non_administrator_before_source_access() -> None:
    service = DatasetValidationService(
        cast(Any, SimpleNamespace()),
        cast(Any, SimpleNamespace()),
        cast(Any, SimpleNamespace()),
        cast(Any, SimpleNamespace()),
        cast(Any, SimpleNamespace()),
        cast(Any, SimpleNamespace()),
    )

    async def exercise() -> None:
        with pytest.raises(QuerySafetyAdministratorRequiredError):
            await service.validate(
                actor=_user(SystemRole.ANALYST),
                dataset_id=uuid4(),
                expected_revision=1,
                request_id="request-override",
                override_reason="This override should never reach the source.",
            )

    asyncio.run(exercise())


def test_policy_rejects_non_positive_thresholds() -> None:
    with pytest.raises(ValueError, match="positive"):
        QuerySafetyPolicy(sort_rows_warning=0)
