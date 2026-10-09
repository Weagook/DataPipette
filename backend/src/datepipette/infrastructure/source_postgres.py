"""Bounded PostgreSQL source connectivity checks."""

import asyncio
import os
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

import psycopg

from datepipette.application.connections import SourceTestOutcome
from datepipette.domain.connections import PostgresConnection


@asynccontextmanager
async def _temporary_ca(certificate: str | None) -> AsyncIterator[str | None]:
    if certificate is None:
        yield None
        return
    descriptor, path = tempfile.mkstemp(prefix="datepipette-ca-", suffix=".pem")
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
            async with _temporary_ca(ca_certificate) as ca_path:
                if ca_path is not None:
                    source = await psycopg.AsyncConnection.connect(
                        host=connection.host,
                        port=connection.port,
                        dbname=connection.database_name,
                        user=connection.username,
                        password=password,
                        sslmode=connection.ssl_mode.value,
                        sslrootcert=ca_path,
                        connect_timeout=self._connect_timeout,
                    )
                else:
                    source = await psycopg.AsyncConnection.connect(
                        host=connection.host,
                        port=connection.port,
                        dbname=connection.database_name,
                        user=connection.username,
                        password=password,
                        sslmode=connection.ssl_mode.value,
                        connect_timeout=self._connect_timeout,
                    )
                async with asyncio.timeout(self._connect_timeout + self._statement_timeout + 1):
                    async with source:
                        async with source.cursor() as cursor:
                            await cursor.execute("SET TRANSACTION READ ONLY")
                            await cursor.execute(
                                "SELECT set_config('statement_timeout', %s, true)",
                                (f"{self._statement_timeout}s",),
                            )
                            await cursor.execute("SELECT 1")
                            await cursor.fetchone()
                        return SourceTestOutcome(
                            succeeded=True,
                            server_version=source.info.server_version,
                        )
        except TimeoutError:
            return SourceTestOutcome(False, "timeout")
        except psycopg.Error as exc:
            if exc.sqlstate is not None and exc.sqlstate.startswith("28"):
                return SourceTestOutcome(False, "authentication_failed")
            if exc.sqlstate == "3D000":
                return SourceTestOutcome(False, "database_not_found")
            internal_message = str(exc).lower()
            if "password authentication failed" in internal_message:
                return SourceTestOutcome(False, "authentication_failed")
            if "database" in internal_message and "does not exist" in internal_message:
                return SourceTestOutcome(False, "database_not_found")
            return SourceTestOutcome(False, "connection_failed")
