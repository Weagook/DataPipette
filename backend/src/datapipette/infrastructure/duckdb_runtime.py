"""Shared admission control for interactive DuckDB work."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Protocol


class InteractiveQueryCapacityExceededError(Exception):
    """The installation-wide interactive query budget has no free slot."""


class InteractiveQueryAdmissionUnavailableError(Exception):
    """The distributed admission authority could not be reached safely."""


class InteractiveQueryAdmission(Protocol):
    def slot(self, maximum_concurrent_queries: int) -> AbstractAsyncContextManager[bool]: ...


class DuckDbQueryConcurrencyLimiter:
    """Apply the captured policy limit across all interactive query types."""

    def __init__(self, admission: InteractiveQueryAdmission | None = None) -> None:
        self._condition = asyncio.Condition()
        self._active = 0
        self._admission = admission

    @asynccontextmanager
    async def slot(
        self,
        maximum_concurrent_queries: int,
        timeout_seconds: float,
    ) -> AsyncIterator[None]:
        if self._admission is not None:
            async with self._admission.slot(maximum_concurrent_queries) as admitted:
                if not admitted:
                    raise InteractiveQueryCapacityExceededError
                yield
            return
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
