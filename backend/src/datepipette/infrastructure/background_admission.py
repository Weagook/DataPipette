"""Crash-safe installation-wide admission for heavy worker jobs."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

_LOCK_NAMESPACE = 1_145_658_452
_ADMISSION_GUARD_SLOT = -1
_MAXIMUM_JOB_SLOTS = 64


class PostgresBackgroundJobAdmission:
    """Share a bounded set of PostgreSQL session advisory locks across workers."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    @asynccontextmanager
    async def slot(self, maximum_concurrent_jobs: int) -> AsyncIterator[bool]:
        async with self._engine.connect() as connection:
            acquired_slot: int | None = None
            await connection.execute(
                text("SELECT pg_advisory_lock(:namespace, :slot)"),
                {"namespace": _LOCK_NAMESPACE, "slot": _ADMISSION_GUARD_SLOT},
            )
            try:
                active = await connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_locks "
                        "WHERE locktype = 'advisory' AND granted "
                        "AND database = ("
                        "SELECT oid FROM pg_database WHERE datname = current_database()"
                        ") "
                        "AND classid = :namespace AND objsubid = 2 "
                        "AND objid BETWEEN 0 AND :last_slot"
                    ),
                    {
                        "namespace": _LOCK_NAMESPACE,
                        "last_slot": _MAXIMUM_JOB_SLOTS - 1,
                    },
                )
                if int(active or 0) < maximum_concurrent_jobs:
                    for slot in range(_MAXIMUM_JOB_SLOTS):
                        acquired = await connection.scalar(
                            text("SELECT pg_try_advisory_lock(:namespace, :slot)"),
                            {"namespace": _LOCK_NAMESPACE, "slot": slot},
                        )
                        if acquired:
                            acquired_slot = slot
                            break
            finally:
                await connection.execute(
                    text("SELECT pg_advisory_unlock(:namespace, :slot)"),
                    {"namespace": _LOCK_NAMESPACE, "slot": _ADMISSION_GUARD_SLOT},
                )
                await connection.commit()
            try:
                yield acquired_slot is not None
            finally:
                if acquired_slot is not None:
                    with suppress(Exception):
                        await connection.execute(
                            text("SELECT pg_advisory_unlock(:namespace, :slot)"),
                            {"namespace": _LOCK_NAMESPACE, "slot": acquired_slot},
                        )
