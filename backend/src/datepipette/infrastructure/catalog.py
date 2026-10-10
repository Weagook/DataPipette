"""Live PostgreSQL catalog inspection through static pg_catalog queries."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import psycopg

from datepipette.application.catalog import SourceCatalogUnavailableError
from datepipette.domain.catalog import (
    CatalogColumn,
    CatalogForeignKey,
    CatalogRelation,
    CatalogRelationDetail,
    RelationKind,
)
from datepipette.domain.connections import PostgresConnection
from datepipette.infrastructure.source_postgres import open_read_only_source

_RELATION_KIND = {
    "r": RelationKind.TABLE,
    "p": RelationKind.PARTITIONED_TABLE,
    "v": RelationKind.VIEW,
    "m": RelationKind.MATERIALIZED_VIEW,
    "f": RelationKind.FOREIGN_TABLE,
}

_SCHEMAS_SQL = """
SELECT namespace.nspname
FROM pg_catalog.pg_namespace AS namespace
WHERE pg_catalog.has_schema_privilege(namespace.oid, 'USAGE')
  AND (%s OR (namespace.nspname !~ '^pg_' AND namespace.nspname <> 'information_schema'))
  AND (%s::text IS NULL OR namespace.nspname > %s::text)
  AND (%s::text[] IS NULL OR namespace.nspname = ANY(%s::text[]))
ORDER BY namespace.nspname
LIMIT %s
"""

_RELATIONS_SQL = """
SELECT relation.relname, relation.relkind
FROM pg_catalog.pg_class AS relation
JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = relation.relnamespace
WHERE namespace.nspname = %s
  AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
  AND pg_catalog.has_schema_privilege(namespace.oid, 'USAGE')
  AND pg_catalog.has_table_privilege(relation.oid, 'SELECT')
  AND (%s::text IS NULL OR relation.relname > %s::text)
  AND (%s::text[] IS NULL OR relation.relname = ANY(%s::text[]))
ORDER BY relation.relname
LIMIT %s
"""

_RELATION_SQL = """
SELECT relation.oid, relation.relkind
FROM pg_catalog.pg_class AS relation
JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = relation.relnamespace
WHERE namespace.nspname = %s
  AND relation.relname = %s
  AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
  AND pg_catalog.has_schema_privilege(namespace.oid, 'USAGE')
  AND pg_catalog.has_table_privilege(relation.oid, 'SELECT')
"""

_COLUMNS_SQL = """
SELECT attribute.attname,
       attribute.attnum,
       pg_catalog.format_type(attribute.atttypid, attribute.atttypmod),
       NOT attribute.attnotnull
FROM pg_catalog.pg_attribute AS attribute
WHERE attribute.attrelid = %s
  AND attribute.attnum > 0
  AND NOT attribute.attisdropped
  AND pg_catalog.has_column_privilege(attribute.attrelid, attribute.attnum, 'SELECT')
ORDER BY attribute.attnum
"""

_PRIMARY_KEY_SQL = """
SELECT attribute.attname
FROM pg_catalog.pg_constraint AS con
JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS key(attnum, position) ON true
JOIN pg_catalog.pg_attribute AS attribute
  ON attribute.attrelid = con.conrelid AND attribute.attnum = key.attnum
WHERE con.conrelid = %s AND con.contype = 'p'
ORDER BY key.position
"""

_FOREIGN_KEYS_SQL = """
SELECT con.conname,
       target_namespace.nspname,
       target_relation.relname,
       array_agg(source_attribute.attname ORDER BY key.position),
       array_agg(target_attribute.attname ORDER BY key.position)
FROM pg_catalog.pg_constraint AS con
JOIN pg_catalog.pg_class AS target_relation ON target_relation.oid = con.confrelid
JOIN pg_catalog.pg_namespace AS target_namespace
  ON target_namespace.oid = target_relation.relnamespace
JOIN LATERAL unnest(con.conkey, con.confkey)
  WITH ORDINALITY AS key(source_attnum, target_attnum, position) ON true
JOIN pg_catalog.pg_attribute AS source_attribute
  ON source_attribute.attrelid = con.conrelid
 AND source_attribute.attnum = key.source_attnum
JOIN pg_catalog.pg_attribute AS target_attribute
  ON target_attribute.attrelid = con.confrelid
 AND target_attribute.attnum = key.target_attnum
WHERE con.conrelid = %s AND con.contype = 'f'
  AND pg_catalog.has_schema_privilege(target_namespace.oid, 'USAGE')
  AND pg_catalog.has_table_privilege(target_relation.oid, 'SELECT')
GROUP BY con.oid, con.conname, target_namespace.nspname, target_relation.relname
ORDER BY con.conname
"""


class PsycopgSourceCatalogInspector:
    def __init__(self, connect_timeout_seconds: int, statement_timeout_seconds: int) -> None:
        self._connect_timeout = connect_timeout_seconds
        self._statement_timeout = statement_timeout_seconds

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
    ) -> list[str]:
        try:
            async with (
                self._source(connection, password, ca_certificate) as source,
                source.cursor() as cursor,
            ):
                await cursor.execute(
                    _SCHEMAS_SQL,
                    (
                        include_system,
                        after_name,
                        after_name,
                        list(allowed_schemas) if allowed_schemas is not None else None,
                        list(allowed_schemas) if allowed_schemas is not None else None,
                        limit,
                    ),
                )
                return [str(row[0]) for row in await cursor.fetchall()]
        except (TimeoutError, psycopg.Error) as exc:
            raise SourceCatalogUnavailableError from exc

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
    ) -> list[CatalogRelation]:
        try:
            async with (
                self._source(connection, password, ca_certificate) as source,
                source.cursor() as cursor,
            ):
                await cursor.execute(
                    _RELATIONS_SQL,
                    (
                        schema_name,
                        after_name,
                        after_name,
                        list(allowed_relations) if allowed_relations is not None else None,
                        list(allowed_relations) if allowed_relations is not None else None,
                        limit,
                    ),
                )
                return [
                    CatalogRelation(
                        schema_name=schema_name,
                        name=str(row[0]),
                        kind=_RELATION_KIND[str(row[1])],
                    )
                    for row in await cursor.fetchall()
                ]
        except (TimeoutError, psycopg.Error, KeyError) as exc:
            raise SourceCatalogUnavailableError from exc

    async def inspect_relation(
        self,
        connection: PostgresConnection,
        *,
        password: str,
        ca_certificate: str | None,
        schema_name: str,
        relation_name: str,
    ) -> CatalogRelationDetail | None:
        try:
            async with (
                self._source(connection, password, ca_certificate) as source,
                source.cursor() as cursor,
            ):
                await cursor.execute(_RELATION_SQL, (schema_name, relation_name))
                relation_row = await cursor.fetchone()
                if relation_row is None:
                    return None
                relation_oid = int(relation_row[0])
                kind = _RELATION_KIND[str(relation_row[1])]
                await cursor.execute(_COLUMNS_SQL, (relation_oid,))
                columns = [
                    CatalogColumn(
                        name=str(row[0]),
                        ordinal_position=int(row[1]),
                        data_type=str(row[2]),
                        nullable=bool(row[3]),
                    )
                    for row in await cursor.fetchall()
                ]
                await cursor.execute(_PRIMARY_KEY_SQL, (relation_oid,))
                primary_key = [str(row[0]) for row in await cursor.fetchall()]
                await cursor.execute(_FOREIGN_KEYS_SQL, (relation_oid,))
                foreign_keys = [
                    CatalogForeignKey(
                        name=str(row[0]),
                        target_schema=str(row[1]),
                        target_relation=str(row[2]),
                        source_columns=list(row[3]),
                        target_columns=list(row[4]),
                    )
                    for row in await cursor.fetchall()
                ]
                return CatalogRelationDetail(
                    relation=CatalogRelation(schema_name, relation_name, kind),
                    columns=columns,
                    primary_key=primary_key,
                    foreign_keys=foreign_keys,
                )
        except (TimeoutError, psycopg.Error, KeyError, TypeError, ValueError) as exc:
            raise SourceCatalogUnavailableError from exc

    @asynccontextmanager
    async def _source(
        self,
        connection: PostgresConnection,
        password: str,
        ca_certificate: str | None,
    ) -> AsyncIterator[psycopg.AsyncConnection[Any]]:
        async with open_read_only_source(
            connection,
            password=password,
            ca_certificate=ca_certificate,
            connect_timeout_seconds=self._connect_timeout,
            statement_timeout_seconds=self._statement_timeout,
        ) as source:
            yield source
