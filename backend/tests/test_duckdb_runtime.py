"""Shared dynamic admission control for interactive DuckDB queries."""

import asyncio

import pytest

from datepipette.infrastructure.duckdb_runtime import DuckDbQueryConcurrencyLimiter


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
