"""Bounded source streaming into immutable local Parquet snapshots."""

import hashlib
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from datepipette.application.connection_access import (
    ConnectionAccessAuthorizer,
    ConnectionAccessDeniedError,
)
from datepipette.application.connections import ConnectionSecretUnavailableError
from datepipette.application.refresh_worker import RefreshExecutionResult
from datepipette.domain.connections import PostgresConnection
from datepipette.domain.datasets import (
    DatasetDefinition,
    DatasetValidation,
    DatasetValidationState,
    dataset_definition_from_document,
    definition_relations,
)
from datepipette.domain.refresh import RefreshRun
from datepipette.domain.snapshots import SnapshotArtifact
from datepipette.infrastructure.crypto import AesGcmSecretCipher
from datepipette.infrastructure.dataset_sql import compile_dataset_select
from datepipette.infrastructure.models import (
    DatasetModel,
    DatasetVersionModel,
    PostgresConnectionModel,
    UserModel,
)
from datepipette.infrastructure.repositories.authentication import to_user
from datepipette.infrastructure.repositories.connections import to_connection
from datepipette.infrastructure.source_postgres import open_read_only_source

_TIMESTAMP = re.compile(r"^timestamp(?:\([0-6]\))? (with|without) time zone$")
_TIME = re.compile(r"^time(?:\([0-6]\))? (with|without) time zone$")
_NUMERIC = re.compile(r"^(?:numeric|decimal)\(([0-9]+),(-?[0-9]+)\)$")
_CHARACTER = re.compile(r"^(?:character varying|varchar|character|char)(?:\([0-9]+\))?$")


class UnsupportedSourceTypeError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class _ExecutionContext:
    connection: PostgresConnection
    password: str
    ca_certificate: str | None
    definition: DatasetDefinition
    validation: DatasetValidation


@dataclass(frozen=True, slots=True)
class _ColumnAdapter:
    name: str
    storage_name: str
    postgres_type: str
    arrow_type: pa.DataType
    nullable: bool
    convert: Callable[[Any], Any]


def _identity(value: Any) -> Any:
    return value


def _text(value: Any) -> str | None:
    return None if value is None else str(value)


def _json_default(value: Any) -> str:
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, (Decimal, UUID, timedelta)):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def _json_text(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(
        value,
        default=_json_default,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _adapter(
    field_id: UUID,
    name: str,
    data_type: str,
    nullable: bool,
) -> _ColumnAdapter:
    normalized = " ".join(data_type.lower().split())
    arrow_type: pa.DataType
    converter: Callable[[Any], Any] = _identity
    if normalized == "boolean":
        arrow_type = pa.bool_()
    elif normalized == "smallint":
        arrow_type = pa.int16()
    elif normalized == "integer":
        arrow_type = pa.int32()
    elif normalized == "bigint":
        arrow_type = pa.int64()
    elif normalized == "real":
        arrow_type = pa.float32()
    elif normalized == "double precision":
        arrow_type = pa.float64()
    elif match := _NUMERIC.fullmatch(normalized):
        precision, scale = int(match.group(1)), int(match.group(2))
        if precision <= 38:
            arrow_type = pa.decimal128(precision, scale)
        elif precision <= 76:
            arrow_type = pa.decimal256(precision, scale)
        else:
            raise UnsupportedSourceTypeError(data_type)
    elif normalized in {
        "numeric",
        "decimal",
        "money",
        "text",
        "name",
        "citext",
        "uuid",
    } or _CHARACTER.fullmatch(normalized):
        arrow_type = pa.string()
        converter = _text
    elif normalized == "date":
        arrow_type = pa.date32()
    elif timestamp_match := _TIMESTAMP.fullmatch(normalized):
        arrow_type = (
            pa.timestamp("us", tz="UTC")
            if timestamp_match.group(1) == "with"
            else pa.timestamp("us")
        )
    elif time_match := _TIME.fullmatch(normalized):
        if time_match.group(1) == "with":
            arrow_type = pa.string()
            converter = _text
        else:
            arrow_type = pa.time64("us")
    elif normalized == "bytea":
        arrow_type = pa.binary()
    elif normalized in {"json", "jsonb"}:
        arrow_type = pa.string()
        converter = _json_text
    elif normalized in {
        "interval",
        "inet",
        "cidr",
        "macaddr",
        "macaddr8",
        "xml",
    }:
        arrow_type = pa.string()
        converter = _text
    else:
        raise UnsupportedSourceTypeError(data_type)
    return _ColumnAdapter(
        name,
        f"field_{field_id.hex}",
        data_type,
        arrow_type,
        nullable,
        converter,
    )


def arrow_schema(validation: DatasetValidation) -> tuple[pa.Schema, tuple[_ColumnAdapter, ...]]:
    adapters = tuple(
        _adapter(field.field_id, field.name, field.data_type, field.nullable)
        for field in validation.result_schema
    )
    fields = [
        pa.field(
            item.storage_name,
            item.arrow_type,
            nullable=item.nullable,
            metadata={b"datepipette:postgres_type": item.postgres_type.encode()},
        )
        for item in adapters
    ]
    return (
        pa.schema(fields, metadata={b"datepipette:schema_version": b"2"}),
        adapters,
    )


def _schema_document(adapters: tuple[_ColumnAdapter, ...]) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "fields": [
            {
                "name": item.name,
                "storage_name": item.storage_name,
                "postgres_type": item.postgres_type,
                "arrow_type": str(item.arrow_type),
                "nullable": item.nullable,
            }
            for item in adapters
        ],
    }


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class LocalParquetSnapshotExecutor:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        cipher: AesGcmSecretCipher,
        storage_root: Path,
        connect_timeout_seconds: int,
        statement_timeout_seconds: int,
        batch_rows: int,
        authorizer: ConnectionAccessAuthorizer,
    ) -> None:
        self._session_factory = session_factory
        self._cipher = cipher
        self._storage_root = storage_root.resolve()
        self._connect_timeout = connect_timeout_seconds
        self._statement_timeout = statement_timeout_seconds
        self._batch_rows = batch_rows
        self._authorizer = authorizer

    async def execute(self, run: RefreshRun) -> RefreshExecutionResult:
        try:
            context = await self._context(run)
            if context is None:
                return RefreshExecutionResult(False, "draft_changed")
            artifact = await self._write(run, context)
            return RefreshExecutionResult(True, snapshot=artifact)
        except UnsupportedSourceTypeError:
            return RefreshExecutionResult(False, "unsupported_source_type")
        except ConnectionSecretUnavailableError:
            return RefreshExecutionResult(False, "connection_secret_unavailable")
        except ConnectionAccessDeniedError:
            return RefreshExecutionResult(False, "connection_access_revoked")
        except (TimeoutError, psycopg.Error):
            return RefreshExecutionResult(False, "source_unavailable")
        except (OSError, pa.ArrowException, ValueError, TypeError):
            return RefreshExecutionResult(False, "snapshot_write_failed")

    async def _context(self, run: RefreshRun) -> _ExecutionContext | None:
        async with self._session_factory() as session:
            dataset = await session.get(DatasetModel, run.dataset_id)
            version = await session.get(DatasetVersionModel, run.dataset_version_id)
            if (
                dataset is None
                or version is None
                or version.revision != run.requested_revision
                or version.validation is None
            ):
                return None
            validation = DatasetValidation.from_document(version.validation)
            if (
                validation.state
                not in {
                    DatasetValidationState.VALID,
                    DatasetValidationState.VALID_WITH_WARNINGS,
                }
                or validation.validated_revision != version.revision
            ):
                return None
            connection_model = await session.get(PostgresConnectionModel, dataset.connection_id)
            actor_model = await session.get(UserModel, run.requested_by)
            if connection_model is None or actor_model is None:
                return None
            definition = dataset_definition_from_document(version.definition)
            await self._authorizer.require_relations(
                actor=to_user(actor_model),
                connection_id=connection_model.id,
                relations=definition_relations(definition),
            )
            connection = to_connection(connection_model)
            password = self._cipher.decrypt(
                connection_model.encrypted_password,
                connection_id=connection.id,
                field="password",
            )
            ca_certificate = (
                self._cipher.decrypt(
                    connection_model.encrypted_ca_certificate,
                    connection_id=connection.id,
                    field="ca_certificate",
                )
                if connection_model.encrypted_ca_certificate is not None
                else None
            )
            return _ExecutionContext(
                connection=connection,
                password=password,
                ca_certificate=ca_certificate,
                definition=definition,
                validation=validation,
            )

    async def _write(
        self,
        run: RefreshRun,
        context: _ExecutionContext,
    ) -> SnapshotArtifact:
        snapshot_id = uuid4()
        object_key = f"datasets/{run.dataset_id}/snapshots/{snapshot_id}.parquet"
        final_path = self._storage_root / object_key
        staging_path = self._storage_root / ".staging" / f"{run.id}-{snapshot_id}.parquet"
        final_path.parent.mkdir(parents=True, exist_ok=True)
        staging_path.parent.mkdir(parents=True, exist_ok=True)
        expected_fields = tuple((field.id, field.name) for field in context.definition.fields)
        validated_fields = tuple(
            (field.field_id, field.name) for field in context.validation.result_schema
        )
        if validated_fields != expected_fields:
            raise ValueError("Validated result schema no longer matches definition")
        schema, adapters = arrow_schema(context.validation)
        row_count = 0
        writer: pq.ParquetWriter | None = None
        try:
            writer = pq.ParquetWriter(
                staging_path,
                schema,
                version="2.6",
                compression="zstd",
                write_statistics=True,
                write_page_checksum=True,
            )
            async with open_read_only_source(
                context.connection,
                password=context.password,
                ca_certificate=context.ca_certificate,
                connect_timeout_seconds=self._connect_timeout,
                statement_timeout_seconds=self._statement_timeout,
            ) as source:
                cursor_name = f"datepipette_refresh_{run.id.hex}"
                async with source.cursor(name=cursor_name) as cursor:
                    await cursor.execute(compile_dataset_select(context.definition))
                    while rows := await cursor.fetchmany(self._batch_rows):
                        if any(len(row) != len(adapters) for row in rows):
                            raise ValueError("Source refresh column count changed")
                        arrays = [
                            pa.array(
                                [
                                    adapter.convert(row[index])
                                    for row in rows
                                    if adapter.nullable or row[index] is not None
                                ],
                                type=adapter.arrow_type,
                                from_pandas=False,
                            )
                            for index, adapter in enumerate(adapters)
                        ]
                        if any(len(array) != len(rows) for array in arrays):
                            raise ValueError("Non-nullable source field returned null")
                        batch = pa.RecordBatch.from_arrays(arrays, schema=schema)
                        writer.write_batch(batch)
                        row_count += batch.num_rows
            writer.close()
            writer = None
            with staging_path.open("rb") as stream:
                os.fsync(stream.fileno())
            parquet_file = pq.ParquetFile(staging_path)
            if parquet_file.metadata is None or parquet_file.metadata.num_rows != row_count:
                raise ValueError("Parquet row count validation failed")
            if not parquet_file.schema_arrow.equals(schema, check_metadata=True):
                raise ValueError("Parquet schema validation failed")
            byte_size = staging_path.stat().st_size
            checksum = hashlib.sha256()
            with staging_path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    checksum.update(chunk)
            os.replace(staging_path, final_path)
            _fsync_directory(final_path.parent)
            return SnapshotArtifact(
                id=snapshot_id,
                storage_backend="local",
                object_key=object_key,
                row_count=row_count,
                byte_size=byte_size,
                checksum_sha256=checksum.hexdigest(),
                schema_document=_schema_document(adapters),
            )
        finally:
            if writer is not None:
                writer.close()
            staging_path.unlink(missing_ok=True)
