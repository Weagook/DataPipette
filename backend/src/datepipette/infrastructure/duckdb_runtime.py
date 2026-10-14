"""Shared admission control for interactive DuckDB work."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class DuckDbQueryConcurrencyLimiter:
    """Apply the captured policy limit across all interactive query types."""

    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._active = 0

    @asynccontextmanager
    async def slot(
        self,
        maximum_concurrent_queries: int,
        timeout_seconds: float,
    ) -> AsyncIterator[None]:
        async with asyncio.timeout(timeout_seconds):
            async with self._condition:
                await self._condition.wait_for(lambda: self._active < maximum_concurrent_queries)
                self._active += 1
        try:
            yield
        finally:
            async with self._condition:
                self._active -= 1
                self._condition.notify_all()
