"""Managed Query Safety policy and override authorization tests."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from datapipette.application.dataset_validation import DatasetValidationService
from datapipette.application.query_safety import (
    ManagedQuerySafetyPolicy,
    QuerySafetyAdministratorRequiredError,
    QuerySafetyPolicyService,
)
from datapipette.domain.datasets import QuerySafetyPolicy
from datapipette.domain.identity import AccountStatus, SystemRole, User

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


def test_policy_cannot_expand_hard_preview_ceilings() -> None:
    with pytest.raises(ValueError, match="row limit"):
        QuerySafetyPolicy(preview_max_rows=201)
    with pytest.raises(ValueError, match="byte limit"):
        QuerySafetyPolicy(preview_max_response_bytes=1_048_577)


def test_policy_enforces_interactive_query_resource_bounds() -> None:
    with pytest.raises(ValueError, match="memory limit"):
        QuerySafetyPolicy(interactive_query_memory_limit_mb=63)
    with pytest.raises(ValueError, match="concurrency"):
        QuerySafetyPolicy(interactive_query_concurrency=65)
    with pytest.raises(ValueError, match="response limit"):
        QuerySafetyPolicy(interactive_query_max_response_bytes=65_535)


def test_policy_enforces_export_and_background_resource_bounds() -> None:
    with pytest.raises(ValueError, match="memory limit"):
        QuerySafetyPolicy(export_memory_limit_mb=63)
    with pytest.raises(ValueError, match="artifact limit"):
        QuerySafetyPolicy(export_max_bytes=65_535)
    with pytest.raises(ValueError, match="background concurrency"):
        QuerySafetyPolicy(background_job_concurrency=65)
