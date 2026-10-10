"""Administrator use cases for live PostgreSQL catalog discovery."""

import base64
import binascii
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from datepipette.application.connection_access import (
    ConnectionAccessAuthorizer,
    ConnectionAccessDeniedError,
)
from datepipette.application.connections import (
    ConnectionSecretUnavailableError,
    ConnectionStore,
    SecretCipher,
)
from datepipette.domain.catalog import (
    CatalogJoinCondition,
    CatalogJoinSuggestion,
    CatalogRelation,
    CatalogRelationDetail,
    CatalogRelationReference,
)
from datepipette.domain.connection_access import ConnectionAccess
from datepipette.domain.connections import PostgresConnection
from datepipette.domain.identity import User


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
        allowed_schemas: tuple[str, ...] | None,
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
        allowed_relations: tuple[str, ...] | None,
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
        authorizer: ConnectionAccessAuthorizer,
    ) -> None:
        self._store = store
        self._cipher = cipher
        self._inspector = inspector
        self._authorizer = authorizer

    async def list_schemas(
        self,
        *,
        actor: User,
        connection_id: UUID,
        cursor: str | None,
        limit: int,
        include_system: bool,
    ) -> CatalogSchemaPage:
        connection, password, ca_certificate, access = await self._resolve(actor, connection_id)
        allowed_schemas = (
            None
            if access.all_access
            else tuple(sorted(access.schemas | {schema for schema, _ in access.tables}))
        )
        items = await self._inspector.list_schemas(
            connection,
            password=password,
            ca_certificate=ca_certificate,
            after_name=_decode_cursor(cursor),
            limit=limit + 1,
            include_system=include_system,
            allowed_schemas=allowed_schemas,
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
        connection, password, ca_certificate, access = await self._resolve(actor, connection_id)
        if not access.allows_schema(schema_name):
            raise CatalogObjectNotFoundError
        allowed_relations = (
            None
            if access.all_access or schema_name in access.schemas
            else tuple(
                sorted(relation for schema, relation in access.tables if schema == schema_name)
            )
        )
        items = await self._inspector.list_relations(
            connection,
            password=password,
            ca_certificate=ca_certificate,
            schema_name=schema_name,
            after_name=_decode_cursor(cursor),
            limit=limit + 1,
            allowed_relations=allowed_relations,
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
        connection, password, ca_certificate, access = await self._resolve(actor, connection_id)
        if not access.allows_relation(schema_name, relation_name):
            raise CatalogObjectNotFoundError
        result = await self._inspector.inspect_relation(
            connection,
            password=password,
            ca_certificate=ca_certificate,
            schema_name=schema_name,
            relation_name=relation_name,
        )
        if result is None:
            raise CatalogObjectNotFoundError
        return CatalogRelationDetail(
            relation=result.relation,
            columns=result.columns,
            primary_key=result.primary_key,
            foreign_keys=[
                foreign_key
                for foreign_key in result.foreign_keys
                if access.allows_relation(
                    foreign_key.target_schema,
                    foreign_key.target_relation,
                )
            ],
        )

    async def suggest_joins(
        self,
        *,
        actor: User,
        connection_id: UUID,
        relations: tuple[CatalogRelationReference, ...],
    ) -> tuple[CatalogJoinSuggestion, ...]:
        if not 1 <= len(relations) <= 10:
            raise ValueError("Between one and ten relations are required")
        relation_ids = [relation.id for relation in relations]
        if len(relation_ids) != len(set(relation_ids)):
            raise ValueError("Relation identifiers must be unique")
        connection, password, ca_certificate, access = await self._resolve(actor, connection_id)
        if any(
            not access.allows_relation(relation.schema_name, relation.relation_name)
            for relation in relations
        ):
            raise CatalogObjectNotFoundError
        by_source_object: dict[tuple[str, str], list[CatalogRelationReference]] = {}
        for relation in relations:
            by_source_object.setdefault((relation.schema_name, relation.relation_name), []).append(
                relation
            )

        suggestions: list[CatalogJoinSuggestion] = []
        for source_reference in relations:
            detail = await self._inspector.inspect_relation(
                connection,
                password=password,
                ca_certificate=ca_certificate,
                schema_name=source_reference.schema_name,
                relation_name=source_reference.relation_name,
            )
            if detail is None:
                raise CatalogObjectNotFoundError
            for foreign_key in detail.foreign_keys:
                if len(foreign_key.source_columns) != len(foreign_key.target_columns):
                    raise SourceCatalogUnavailableError
                for target_reference in by_source_object.get(
                    (foreign_key.target_schema, foreign_key.target_relation), []
                ):
                    if source_reference.id == target_reference.id:
                        continue
                    suggestions.append(
                        CatalogJoinSuggestion(
                            foreign_key_name=foreign_key.name,
                            source_relation_id=source_reference.id,
                            target_relation_id=target_reference.id,
                            conditions=tuple(
                                CatalogJoinCondition(source_column, target_column)
                                for source_column, target_column in zip(
                                    foreign_key.source_columns,
                                    foreign_key.target_columns,
                                    strict=True,
                                )
                            ),
                        )
                    )
        return tuple(suggestions)

    async def _resolve(
        self, actor: User, connection_id: UUID
    ) -> tuple[PostgresConnection, str, str | None, ConnectionAccess]:
        try:
            access = await self._authorizer.require_connection(
                actor=actor,
                connection_id=connection_id,
            )
        except ConnectionAccessDeniedError as exc:
            raise CatalogConnectionNotFoundError from exc
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
        return connection, password, ca_certificate, access
