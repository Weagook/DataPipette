"""Secure PostgreSQL source connection use cases."""

import base64
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from datapipette.application.connection_access import (
    ConnectionAccessAuthorizer,
    ConnectionAccessDeniedError,
    ConnectionAccessStore,
)
from datapipette.domain.connections import (
    ConnectionTestResult,
    EncryptedConnectionSecrets,
    PostgresConnection,
    PostgresSslMode,
)
from datapipette.domain.identity import AccountStatus, SystemRole, User


class AdministratorRequiredError(Exception):
    pass


class ConnectionAlreadyExistsError(Exception):
    pass


class ConnectionNotFoundError(Exception):
    pass


class InvalidConnectionCursorError(Exception):
    pass


class ConnectionSecretUnavailableError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class ConnectionCreate:
    name: str
    host: str
    port: int
    database_name: str
    username: str
    password: str
    ssl_mode: PostgresSslMode
    ca_certificate: str | None


@dataclass(frozen=True, slots=True)
class ConnectionUpdate:
    name: str | None = None
    host: str | None = None
    port: int | None = None
    database_name: str | None = None
    username: str | None = None
    password: str | None = None
    ssl_mode: PostgresSslMode | None = None
    ca_certificate: str | None = None
    ca_certificate_set: bool = False


@dataclass(frozen=True, slots=True)
class ConnectionPage:
    connections: list[PostgresConnection]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class SourceTestOutcome:
    succeeded: bool
    error_code: str | None = None
    server_version: int | None = None


class SecretCipher(Protocol):
    def encrypt(self, plaintext: str, *, connection_id: UUID, field: str) -> str: ...

    def decrypt(self, envelope: str, *, connection_id: UUID, field: str) -> str: ...


class SourceConnectionTester(Protocol):
    async def test(
        self,
        connection: PostgresConnection,
        *,
        password: str,
        ca_certificate: str | None,
    ) -> SourceTestOutcome: ...


class ConnectionStore(Protocol):
    async def create(
        self,
        *,
        connection_id: UUID,
        actor_id: UUID,
        value: ConnectionCreate,
        secrets: EncryptedConnectionSecrets,
        occurred_at: datetime,
        request_id: str,
    ) -> PostgresConnection: ...

    async def list(
        self,
        *,
        after_created_at: datetime | None,
        after_id: UUID | None,
        allowed_ids: frozenset[UUID] | None,
        limit: int,
    ) -> list[PostgresConnection]: ...

    async def get(self, connection_id: UUID) -> PostgresConnection | None: ...

    async def get_secrets(self, connection_id: UUID) -> EncryptedConnectionSecrets | None: ...

    async def update(
        self,
        *,
        connection_id: UUID,
        actor_id: UUID,
        value: ConnectionUpdate,
        encrypted_password: str | None,
        encrypted_ca_certificate: str | None,
        occurred_at: datetime,
        request_id: str,
    ) -> PostgresConnection | None: ...

    async def record_test(
        self,
        *,
        connection_id: UUID,
        actor_id: UUID,
        result: ConnectionTestResult,
        request_id: str,
    ) -> None: ...


def _require_admin(actor: User) -> None:
    if actor.role is not SystemRole.ADMIN or actor.status is not AccountStatus.ACTIVE:
        raise AdministratorRequiredError


def _text(value: str, label: str, maximum: int) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} must not be empty")
    if len(normalized) > maximum:
        raise ValueError(f"{label} is too long")
    return normalized


def _validate_tls(mode: PostgresSslMode, ca_certificate: str | None) -> None:
    if mode in {PostgresSslMode.VERIFY_CA, PostgresSslMode.VERIFY_FULL} and not ca_certificate:
        raise ValueError("Certificate-verifying SSL modes require a CA certificate")


def _cursor(connection: PostgresConnection) -> str:
    value = f"{connection.created_at.isoformat()}|{connection.id}".encode()
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode_cursor(value: str | None) -> tuple[datetime | None, UUID | None]:
    if value is None:
        return None, None
    try:
        padding = "=" * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode(value + padding).decode()
        timestamp, identifier = decoded.split("|", maxsplit=1)
        created_at = datetime.fromisoformat(timestamp)
        if created_at.tzinfo is None:
            raise ValueError
        return created_at, UUID(identifier)
    except (UnicodeDecodeError, ValueError) as exc:
        raise InvalidConnectionCursorError from exc


class ConnectionService:
    def __init__(
        self,
        store: ConnectionStore,
        cipher: SecretCipher,
        tester: SourceConnectionTester,
        access_store: ConnectionAccessStore,
        authorizer: ConnectionAccessAuthorizer,
    ) -> None:
        self._store = store
        self._cipher = cipher
        self._tester = tester
        self._access_store = access_store
        self._authorizer = authorizer

    async def create(
        self, *, actor: User, value: ConnectionCreate, request_id: str
    ) -> PostgresConnection:
        _require_admin(actor)
        normalized = ConnectionCreate(
            name=_text(value.name, "Name", 100),
            host=_text(value.host, "Host", 253),
            port=value.port,
            database_name=_text(value.database_name, "Database name", 255),
            username=_text(value.username, "Username", 255),
            password=value.password,
            ssl_mode=value.ssl_mode,
            ca_certificate=value.ca_certificate.strip() if value.ca_certificate else None,
        )
        _validate_tls(normalized.ssl_mode, normalized.ca_certificate)
        connection_id = uuid4()
        secrets = EncryptedConnectionSecrets(
            password=self._cipher.encrypt(
                normalized.password, connection_id=connection_id, field="password"
            ),
            ca_certificate=(
                self._cipher.encrypt(
                    normalized.ca_certificate,
                    connection_id=connection_id,
                    field="ca_certificate",
                )
                if normalized.ca_certificate is not None
                else None
            ),
        )
        return await self._store.create(
            connection_id=connection_id,
            actor_id=actor.id,
            value=normalized,
            secrets=secrets,
            occurred_at=datetime.now(UTC),
            request_id=request_id,
        )

    async def list(self, *, actor: User, cursor: str | None, limit: int) -> ConnectionPage:
        if actor.status is not AccountStatus.ACTIVE or actor.role is SystemRole.VIEWER:
            return ConnectionPage([], None)
        after_created_at, after_id = _decode_cursor(cursor)
        allowed_ids = (
            None
            if actor.role is SystemRole.ADMIN
            else await self._access_store.list_accessible_connection_ids(actor.id)
        )
        items = await self._store.list(
            after_created_at=after_created_at,
            after_id=after_id,
            allowed_ids=allowed_ids,
            limit=limit + 1,
        )
        visible = items[:limit]
        return ConnectionPage(
            connections=visible,
            next_cursor=_cursor(visible[-1]) if len(items) > limit else None,
        )

    async def get(self, *, actor: User, connection_id: UUID) -> PostgresConnection:
        try:
            await self._authorizer.require_connection(actor=actor, connection_id=connection_id)
        except ConnectionAccessDeniedError as exc:
            raise ConnectionNotFoundError from exc
        connection = await self._store.get(connection_id)
        if connection is None:
            raise ConnectionNotFoundError
        return connection

    async def update(
        self,
        *,
        actor: User,
        connection_id: UUID,
        value: ConnectionUpdate,
        request_id: str,
    ) -> PostgresConnection:
        _require_admin(actor)
        current = await self._store.get(connection_id)
        secrets = await self._store.get_secrets(connection_id)
        if current is None or secrets is None:
            raise ConnectionNotFoundError
        next_ca = value.ca_certificate.strip() if value.ca_certificate else None
        if not value.ca_certificate_set:
            next_ca = self._decrypt_optional(
                secrets.ca_certificate, connection_id, "ca_certificate"
            )
        next_mode = value.ssl_mode or current.ssl_mode
        _validate_tls(next_mode, next_ca)
        encrypted_password = (
            self._cipher.encrypt(value.password, connection_id=connection_id, field="password")
            if value.password is not None
            else None
        )
        encrypted_ca = (
            self._cipher.encrypt(next_ca, connection_id=connection_id, field="ca_certificate")
            if value.ca_certificate_set and next_ca is not None
            else None
        )
        updated = await self._store.update(
            connection_id=connection_id,
            actor_id=actor.id,
            value=value,
            encrypted_password=encrypted_password,
            encrypted_ca_certificate=encrypted_ca,
            occurred_at=datetime.now(UTC),
            request_id=request_id,
        )
        if updated is None:
            raise ConnectionNotFoundError
        return updated

    async def test(
        self, *, actor: User, connection_id: UUID, request_id: str
    ) -> ConnectionTestResult:
        _require_admin(actor)
        connection = await self.get(actor=actor, connection_id=connection_id)
        secrets = await self._store.get_secrets(connection_id)
        if secrets is None:
            raise ConnectionNotFoundError
        try:
            password = self._cipher.decrypt(
                secrets.password, connection_id=connection_id, field="password"
            )
            ca_certificate = self._decrypt_optional(
                secrets.ca_certificate, connection_id, "ca_certificate"
            )
            outcome = await self._tester.test(
                connection,
                password=password,
                ca_certificate=ca_certificate,
            )
        except ConnectionSecretUnavailableError:
            outcome = SourceTestOutcome(False, "secret_unavailable")
        result = ConnectionTestResult(
            succeeded=outcome.succeeded,
            tested_at=datetime.now(UTC),
            error_code=outcome.error_code,
            server_version=outcome.server_version,
        )
        await self._store.record_test(
            connection_id=connection_id,
            actor_id=actor.id,
            result=result,
            request_id=request_id,
        )
        return result

    def _decrypt_optional(self, value: str | None, connection_id: UUID, field: str) -> str | None:
        if value is None:
            return None
        return self._cipher.decrypt(value, connection_id=connection_id, field=field)
