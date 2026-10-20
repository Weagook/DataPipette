"""Bounded, read-only PostgreSQL source sessions."""

import asyncio
import os
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

import psycopg

from datapipette.application.connections import SourceTestOutcome
from datapipette.domain.connections import PostgresConnection


@asynccontextmanager
async def _temporary_ca(certificate: str | None) -> AsyncIterator[str | None]:
    if certificate is None:
        yield None
        return
    descriptor, path = tempfile.mkstemp(prefix="datapipette-ca-", suffix=".pem")
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as temporary_file:
            temporary_file.write(certificate.encode())
        descriptor = -1
        yield path
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        with suppress(FileNotFoundError):
            os.unlink(path)


@asynccontextmanager
async def open_read_only_source(
    connection: PostgresConnection,
    *,
    password: str,
    ca_certificate: str | None,
    connect_timeout_seconds: int,
    statement_timeout_seconds: int,
) -> AsyncIterator[psycopg.AsyncConnection[Any]]:
    """Open one transaction that cannot write and cannot run without a deadline."""

    async with _temporary_ca(ca_certificate) as ca_path:
        async with asyncio.timeout(connect_timeout_seconds + statement_timeout_seconds + 1):
            if ca_path is None:
                source = await psycopg.AsyncConnection.connect(
                    host=connection.host,
                    port=connection.port,
                    dbname=connection.database_name,
                    user=connection.username,
                    password=password,
                    sslmode=connection.ssl_mode.value,
                    connect_timeout=connect_timeout_seconds,
                )
            else:
                source = await psycopg.AsyncConnection.connect(
                    host=connection.host,
                    port=connection.port,
                    dbname=connection.database_name,
                    user=connection.username,
                    password=password,
                    sslmode=connection.ssl_mode.value,
                    sslrootcert=ca_path,
                    connect_timeout=connect_timeout_seconds,
                )
            async with source:
                async with source.cursor() as cursor:
                    await cursor.execute("SET TRANSACTION READ ONLY")
                    await cursor.execute(
                        "SELECT set_config('statement_timeout', %s, true)",
                        (f"{statement_timeout_seconds}s",),
                    )
                yield source


def source_error_code(exc: psycopg.Error) -> str:
    """Map internal driver details to a small safe vocabulary."""

    if exc.sqlstate is not None and exc.sqlstate.startswith("28"):
        return "authentication_failed"
    if exc.sqlstate == "3D000":
        return "database_not_found"
    internal_message = str(exc).lower()
    if "password authentication failed" in internal_message:
        return "authentication_failed"
    if "database" in internal_message and "does not exist" in internal_message:
        return "database_not_found"
    return "connection_failed"


class PsycopgSourceConnectionTester:
    def __init__(self, connect_timeout_seconds: int, statement_timeout_seconds: int) -> None:
        self._connect_timeout = connect_timeout_seconds
        self._statement_timeout = statement_timeout_seconds

    async def test(
        self,
        connection: PostgresConnection,
        *,
        password: str,
        ca_certificate: str | None,
    ) -> SourceTestOutcome:
        try:
            async with open_read_only_source(
                connection,
                password=password,
                ca_certificate=ca_certificate,
                connect_timeout_seconds=self._connect_timeout,
                statement_timeout_seconds=self._statement_timeout,
            ) as source:
                async with source.cursor() as cursor:
                    await cursor.execute("SELECT 1")
                    await cursor.fetchone()
                return SourceTestOutcome(
                    succeeded=True,
                    server_version=source.info.server_version,
                )
        except TimeoutError:
            return SourceTestOutcome(False, "timeout")
        except psycopg.Error as exc:
            return SourceTestOutcome(False, source_error_code(exc))
