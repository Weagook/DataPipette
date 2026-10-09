"""PostgreSQL source connection vocabulary."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class PostgresSslMode(StrEnum):
    DISABLE = "disable"
    REQUIRE = "require"
    VERIFY_CA = "verify-ca"
    VERIFY_FULL = "verify-full"


@dataclass(frozen=True, slots=True)
class PostgresConnection:
    id: UUID
    name: str
    host: str
    port: int
    database_name: str
    username: str
    ssl_mode: PostgresSslMode
    has_ca_certificate: bool
    created_by: UUID
    last_tested_at: datetime | None
    last_test_succeeded: bool | None
    last_test_error_code: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class EncryptedConnectionSecrets:
    password: str
    ca_certificate: str | None


@dataclass(frozen=True, slots=True)
class ConnectionTestResult:
    succeeded: bool
    tested_at: datetime
    error_code: str | None = None
    server_version: int | None = None
