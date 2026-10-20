"""Infrastructure readiness behavior."""

import asyncio
import logging
from types import TracebackType
from typing import Self, cast

import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from datapipette.infrastructure.health import InfrastructureReadinessProbe


class FakeConnection:
    def __init__(self, *, error: Exception | None = None) -> None:
        self._error = error

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    async def execute(self, _statement: object) -> None:
        if self._error is not None:
            raise self._error


class FakeEngine:
    def __init__(self, *, error: Exception | None = None) -> None:
        self._error = error

    def connect(self) -> FakeConnection:
        return FakeConnection(error=self._error)


class FakeRedis:
    def __init__(self, *, error: Exception | None = None) -> None:
        self._error = error

    async def ping(self) -> bool:
        if self._error is not None:
            raise self._error
        return True


def create_probe(
    *,
    database_error: Exception | None = None,
    redis_error: Exception | None = None,
) -> InfrastructureReadinessProbe:
    return InfrastructureReadinessProbe(
        database_engine=cast(AsyncEngine, FakeEngine(error=database_error)),
        redis_client=cast(Redis, FakeRedis(error=redis_error)),
        timeout_seconds=0.1,
    )


def test_probe_reports_available_dependencies() -> None:
    result = asyncio.run(create_probe().check())

    assert result.ready is True
    assert result.checks["metadata_database"].available is True
    assert result.checks["redis"].available is True


def test_probe_converts_dependency_errors_to_safe_states(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    result = asyncio.run(
        create_probe(
            database_error=RuntimeError("database secret details"),
            redis_error=RuntimeError("redis secret details"),
        ).check()
    )

    assert result.ready is False
    assert result.checks["metadata_database"].available is False
    assert result.checks["redis"].available is False
    assert "secret details" not in caplog.text
