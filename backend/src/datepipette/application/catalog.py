"""Administrator use cases for live PostgreSQL catalog discovery."""

import base64
import binascii
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from datepipette.application.connections import (
    ConnectionSecretUnavailableError,
    ConnectionStore,
    SecretCipher,
)
from datepipette.domain.catalog import CatalogRelation, CatalogRelationDetail
from datepipette.domain.connections import PostgresConnection
from datepipette.domain.identity import AccountStatus, SystemRole, User


class CatalogAdministratorRequiredError(Exception):
    pass


class CatalogConnectionNotFoundError(Exception):
    pass


class CatalogObjectNotFoundError(Exception):
    pass


class InvalidCatalogCursorError(Exception):
    pass


class SourceCatalogUnavailableError(Exception):
    """A redacted boundary error for any source connectivity or query failure."""


class CatalogSecretUnavailableError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class CatalogSchemaPage:
    schemas: list[str]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class CatalogRelationPage:
    relations: list[CatalogRelation]
    next_cursor: str | None


class SourceCatalogInspector(Protocol):
    async def list_schemas(
        self,
        connection: PostgresConnection,
        *,
        password: str,
        ca_certificate: str | None,
        after_name: str | None,
        limit: int,
        include_system: bool,
    ) -> list[str]: ...

    async def list_relations(
        self,
        connection: PostgresConnection,
        *,
        password: str,
        ca_certificate: str | None,
        schema_name: str,
        after_name: str | None,
        limit: int,
    ) -> list[CatalogRelation]: ...

    async def inspect_relation(
        self,
        connection: PostgresConnection,
        *,
        password: str,
        ca_certificate: str | None,
        schema_name: str,
        relation_name: str,
    ) -> CatalogRelationDetail | None: ...


def _encode_cursor(name: str) -> str:
    return base64.urlsafe_b64encode(name.encode()).decode().rstrip("=")


def _decode_cursor(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        padding = "=" * (-len(value) % 4)
        decoded = base64.b64decode(value + padding, altchars=b"-_", validate=True).decode()
        if not decoded or "\x00" in decoded:
            raise ValueError
        return decoded
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise InvalidCatalogCursorError from exc


class CatalogService:
    def __init__(
        self,
        store: ConnectionStore,
        cipher: SecretCipher,
        inspector: SourceCatalogInspector,
    ) -> None:
        self._store = store
        self._cipher = cipher
        self._inspector = inspector

    async def list_schemas(
        self,
        *,
        actor: User,
        connection_id: UUID,
        cursor: str | None,
        limit: int,
        include_system: bool,
    ) -> CatalogSchemaPage:
        connection, password, ca_certificate = await self._resolve(actor, connection_id)
        items = await self._inspector.list_schemas(
            connection,
            password=password,
            ca_certificate=ca_certificate,
            after_name=_decode_cursor(cursor),
            limit=limit + 1,
            include_system=include_system,
        )
        visible = items[:limit]
        return CatalogSchemaPage(
            schemas=visible,
            next_cursor=_encode_cursor(visible[-1]) if len(items) > limit else None,
        )

    async def list_relations(
        self,
        *,
        actor: User,
        connection_id: UUID,
        schema_name: str,
        cursor: str | None,
        limit: int,
    ) -> CatalogRelationPage:
        connection, password, ca_certificate = await self._resolve(actor, connection_id)
        items = await self._inspector.list_relations(
            connection,
            password=password,
            ca_certificate=ca_certificate,
            schema_name=schema_name,
            after_name=_decode_cursor(cursor),
            limit=limit + 1,
        )
        visible = items[:limit]
        return CatalogRelationPage(
            relations=visible,
            next_cursor=_encode_cursor(visible[-1].name) if len(items) > limit else None,
        )

    async def inspect_relation(
        self,
        *,
        actor: User,
        connection_id: UUID,
        schema_name: str,
        relation_name: str,
    ) -> CatalogRelationDetail:
        connection, password, ca_certificate = await self._resolve(actor, connection_id)
        result = await self._inspector.inspect_relation(
            connection,
            password=password,
            ca_certificate=ca_certificate,
            schema_name=schema_name,
            relation_name=relation_name,
        )
        if result is None:
            raise CatalogObjectNotFoundError
        return result

    async def _resolve(
        self, actor: User, connection_id: UUID
    ) -> tuple[PostgresConnection, str, str | None]:
        if actor.role is not SystemRole.ADMIN or actor.status is not AccountStatus.ACTIVE:
            raise CatalogAdministratorRequiredError
        connection = await self._store.get(connection_id)
        secrets = await self._store.get_secrets(connection_id)
        if connection is None or secrets is None:
            raise CatalogConnectionNotFoundError
        try:
            password = self._cipher.decrypt(
                secrets.password, connection_id=connection_id, field="password"
            )
            ca_certificate = (
                self._cipher.decrypt(
                    secrets.ca_certificate,
                    connection_id=connection_id,
                    field="ca_certificate",
                )
                if secrets.ca_certificate is not None
                else None
            )
        except ConnectionSecretUnavailableError as exc:
            raise CatalogSecretUnavailableError from exc
        return connection, password, ca_certificate
