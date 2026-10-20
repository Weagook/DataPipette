"""Shared dynamic admission control for interactive DuckDB queries."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest

from datapipette.infrastructure.duckdb_runtime import (
    DuckDbQueryConcurrencyLimiter,
    InteractiveQueryAdmissionUnavailableError,
    InteractiveQueryCapacityExceededError,
)


def test_limiter_shares_capacity_and_applies_each_captured_limit() -> None:
    async def exercise() -> None:
        limiter = DuckDbQueryConcurrencyLimiter()
        first_entered = asyncio.Event()
        release_first = asyncio.Event()

        async def first_query() -> None:
            async with limiter.slot(1, 1):
                first_entered.set()
                await release_first.wait()

        first = asyncio.create_task(first_query())
        await first_entered.wait()
        with pytest.raises(TimeoutError):
            async with limiter.slot(1, 0.001):
                pytest.fail("A second query exceeded the captured concurrency limit")

        # A later request may observe a newer policy revision with a higher limit.
        async with limiter.slot(2, 1):
            pass

        release_first.set()
        await first
        async with limiter.slot(1, 1):
            pass

    asyncio.run(exercise())


class FakeAdmission:
    def __init__(self, *, admitted: bool = True, unavailable: bool = False) -> None:
        self.admitted = admitted
        self.unavailable = unavailable
        self.limits: list[int] = []

    @asynccontextmanager
    async def slot(self, maximum_concurrent_queries: int) -> AsyncIterator[bool]:
        self.limits.append(maximum_concurrent_queries)
        if self.unavailable:
            raise InteractiveQueryAdmissionUnavailableError
        yield self.admitted


def test_distributed_limiter_fails_fast_and_preserves_admission_failure() -> None:
    async def exercise() -> None:
        rejected = FakeAdmission(admitted=False)
        limiter = DuckDbQueryConcurrencyLimiter(rejected)
        with pytest.raises(InteractiveQueryCapacityExceededError):
            async with limiter.slot(3, 60):
                pytest.fail("A rejected distributed query entered execution")
        assert rejected.limits == [3]

        unavailable = DuckDbQueryConcurrencyLimiter(FakeAdmission(unavailable=True))
        with pytest.raises(InteractiveQueryAdmissionUnavailableError):
            async with unavailable.slot(2, 60):
                pytest.fail("An unavailable admission authority entered execution")

    asyncio.run(exercise())
