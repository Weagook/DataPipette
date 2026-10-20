"""Focused source-query cancellation behavior."""

import asyncio
from typing import Any, cast

import psycopg

from datapipette.infrastructure.parquet_snapshots import _cancel_source_when_requested


class FakeAsyncSource:
    def __init__(self) -> None:
        self.cancelled = False

    async def cancel_safe(self) -> None:
        self.cancelled = True


def test_source_query_is_cancelled_when_worker_signal_arrives() -> None:
    source = FakeAsyncSource()
    cancellation = asyncio.Event()

    async def exercise() -> None:
        task = asyncio.create_task(
            _cancel_source_when_requested(
                cast(psycopg.AsyncConnection[Any], source),
                cancellation,
            )
        )
        await asyncio.sleep(0)
        assert source.cancelled is False
        cancellation.set()
        await task

    asyncio.run(exercise())
    assert source.cancelled is True
