"""Crash-safe installation-wide admission for interactive analytical queries."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from datapipette.infrastructure.duckdb_runtime import (
    InteractiveQueryAdmissionUnavailableError,
)

_LOCK_NAMESPACE = 1_145_658_453
_ADMISSION_GUARD_SLOT = -1
_MAXIMUM_QUERY_SLOTS = 64


class PostgresInteractiveQueryAdmission:
    """Share immediate PostgreSQL session-lock admission across API instances."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    @asynccontextmanager
    async def slot(self, maximum_concurrent_queries: int) -> AsyncIterator[bool]:
        connection = None
        try:
            connection = await self._engine.connect()
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
                        "last_slot": _MAXIMUM_QUERY_SLOTS - 1,
                    },
                )
                if int(active or 0) < maximum_concurrent_queries:
                    for slot in range(_MAXIMUM_QUERY_SLOTS):
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
        except SQLAlchemyError as exc:
            if connection is not None:
                with suppress(Exception):
                    await connection.close()
            raise InteractiveQueryAdmissionUnavailableError from exc

        try:
            yield acquired_slot is not None
        finally:
            if acquired_slot is not None:
                with suppress(Exception):
                    await connection.execute(
                        text("SELECT pg_advisory_unlock(:namespace, :slot)"),
                        {"namespace": _LOCK_NAMESPACE, "slot": acquired_slot},
                    )
            with suppress(Exception):
                await connection.close()
