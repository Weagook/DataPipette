"""Bounded source streaming into immutable Parquet snapshot objects."""

import asyncio
import hashlib
import json
import os
import re
import tempfile
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode
from uuid import UUID, uuid4

import adbc_driver_postgresql  # type: ignore[import-untyped]
import adbc_driver_postgresql.dbapi as adbc_postgresql  # type: ignore[import-untyped]
import psycopg
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from adbc_driver_manager import dbapi as adbc_dbapi
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
from datepipette.infrastructure.object_storage import (
    LocalObjectStorage,
    ObjectStorage,
    ObjectStorageUnavailableError,
)
from datepipette.infrastructure.repositories.authentication import to_user
from datepipette.infrastructure.repositories.connections import to_connection

_TIMESTAMP = re.compile(r"^timestamp(?:\([0-6]\))? (with|without) time zone$")
_TIME = re.compile(r"^time(?:\([0-6]\))? (with|without) time zone$")
_NUMERIC = re.compile(r"^(?:numeric|decimal)\(([0-9]+),(-?[0-9]+)\)$")
_CHARACTER = re.compile(r"^(?:character varying|varchar|character|char)(?:\([0-9]+\))?$")


class UnsupportedSourceTypeError(ValueError):
    pass


class RefreshCancelledError(Exception):
    pass


class RefreshRowLimitExceededError(Exception):
    pass


def _raise_if_cancelled(cancellation: asyncio.Event) -> None:
    if cancellation.is_set():
        raise RefreshCancelledError


class _AdbcCancelHandle:
    """Expose cooperative ADBC cancellation without racing connection teardown."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._connection: Any | None = None

    def attach(self, connection: Any) -> None:
        with self._lock:
            self._connection = connection

    def detach(self) -> None:
        with self._lock:
            self._connection = None

    def cancel(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.adbc_cancel()


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
    if isinstance(value, str):
        value = json.loads(value)
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
        "uuid",
    }:
        arrow_type = pa.string()
        converter = _text
    elif normalized in {"text", "name", "citext"} or _CHARACTER.fullmatch(normalized):
        arrow_type = pa.string()
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


def _record_batch_from_rows(
    rows: Sequence[Sequence[Any]],
    *,
    schema: pa.Schema,
    adapters: tuple[_ColumnAdapter, ...],
) -> pa.RecordBatch:
    """Build one Arrow batch without a Python callback for every native cell."""

    if any(len(row) != len(adapters) for row in rows):
        raise ValueError("Source refresh column count changed")
    arrays: list[pa.Array] = []
    columns = zip(*rows, strict=True)
    for adapter, values in zip(adapters, columns, strict=True):
        source_values = values if adapter.convert is _identity else map(adapter.convert, values)
        array = pa.array(
            source_values,
            type=adapter.arrow_type,
            from_pandas=False,
        )
        if not adapter.nullable and array.null_count:
            raise ValueError("Non-nullable source field returned null")
        arrays.append(array)
    return pa.RecordBatch.from_arrays(arrays, schema=schema)


@contextmanager
def _temporary_ca_file(certificate: str | None) -> Iterator[str | None]:
    if certificate is None:
        yield None
        return
    descriptor, path = tempfile.mkstemp(prefix="datepipette-adbc-ca-", suffix=".pem")
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


def _postgres_uri(
    connection: PostgresConnection,
    *,
    password: str,
    ca_path: str | None,
    connect_timeout_seconds: int,
) -> str:
    host = f"[{connection.host}]" if ":" in connection.host else connection.host
    query: dict[str, str] = {
        "connect_timeout": str(connect_timeout_seconds),
        "sslmode": connection.ssl_mode.value,
    }
    if ca_path is not None:
        query["sslrootcert"] = ca_path
    return (
        f"postgresql://{quote(connection.username, safe='')}:{quote(password, safe='')}"
        f"@{host}:{connection.port}/{quote(connection.database_name, safe='')}?"
        f"{urlencode(query)}"
    )


def _coerce_arrow_batch(
    batch: pa.RecordBatch,
    *,
    schema: pa.Schema,
    adapters: tuple[_ColumnAdapter, ...],
) -> pa.RecordBatch:
    if batch.num_columns != len(adapters):
        raise ValueError("Source refresh column count changed")
    if tuple(batch.schema.names) != tuple(adapter.name for adapter in adapters):
        raise ValueError("Source refresh column names changed")
    arrays: list[pa.Array] = []
    for index, adapter in enumerate(adapters):
        array = batch.column(index)
        if adapter.convert is not _identity:
            array = pa.array(
                map(adapter.convert, array.to_pylist()),
                type=adapter.arrow_type,
                from_pandas=False,
            )
        elif array.type != adapter.arrow_type:
            array = pc.cast(array, adapter.arrow_type, safe=True)
        if not adapter.nullable and array.null_count:
            raise ValueError("Non-nullable source field returned null")
        arrays.append(array)
    return pa.RecordBatch.from_arrays(arrays, schema=schema)


def _stream_arrow_snapshot(
    staging_path: Path,
    *,
    connection: PostgresConnection,
    password: str,
    ca_certificate: str | None,
    connect_timeout_seconds: int,
    statement_timeout_seconds: int,
    batch_rows: int,
    maximum_rows: int,
    query: str,
    schema: pa.Schema,
    adapters: tuple[_ColumnAdapter, ...],
    cancellation: threading.Event,
    cancel_handle: _AdbcCancelHandle,
) -> int:
    row_count = 0
    # ADBC accepts a byte target rather than a row target. Eight bytes per cell is
    # a conservative baseline, with bounds keeping each Arrow batch predictable.
    batch_bytes = min(max(batch_rows * max(len(adapters), 1) * 8, 1024 * 1024), 64 << 20)
    writer = pq.ParquetWriter(
        staging_path,
        schema,
        version="2.6",
        compression="zstd",
        write_statistics=True,
        write_page_checksum=True,
    )
    try:
        with _temporary_ca_file(ca_certificate) as ca_path:
            uri = _postgres_uri(
                connection,
                password=password,
                ca_path=ca_path,
                connect_timeout_seconds=connect_timeout_seconds,
            )
            with adbc_postgresql.connect(uri) as source:
                cancel_handle.attach(source)
                try:
                    with source.cursor() as policy_cursor:
                        policy_cursor.execute("SET TRANSACTION READ ONLY")
                        policy_cursor.execute(
                            f"SET LOCAL statement_timeout = '{statement_timeout_seconds}s'"
                        )
                    with source.cursor(
                        adbc_stmt_kwargs={
                            adbc_driver_postgresql.StatementOptions.BATCH_SIZE_HINT_BYTES.value: (
                                str(batch_bytes)
                            )
                        }
                    ) as cursor:
                        if cancellation.is_set():
                            raise RefreshCancelledError
                        cursor.execute(query)
                        reader = cursor.fetch_record_batch()
                        if tuple(reader.schema.names) != tuple(
                            adapter.name for adapter in adapters
                        ):
                            raise ValueError("Source refresh column names changed")
                        for source_batch in reader:
                            if cancellation.is_set():
                                raise RefreshCancelledError
                            if row_count + source_batch.num_rows > maximum_rows:
                                raise RefreshRowLimitExceededError
                            batch = _coerce_arrow_batch(
                                source_batch,
                                schema=schema,
                                adapters=adapters,
                            )
                            writer.write_batch(batch, row_group_size=batch_rows)
                            row_count += batch.num_rows
                finally:
                    cancel_handle.detach()
    finally:
        writer.close()
    return row_count


class ParquetSnapshotExecutor:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        cipher: AesGcmSecretCipher,
        storage: ObjectStorage,
        work_root: Path,
        connect_timeout_seconds: int,
        statement_timeout_seconds: int,
        batch_rows: int,
        authorizer: ConnectionAccessAuthorizer,
        maximum_rows: int = 10_000_000,
    ) -> None:
        self._session_factory = session_factory
        self._cipher = cipher
        self._storage = storage
        self._work_root = work_root.resolve()
        self._connect_timeout = connect_timeout_seconds
        self._statement_timeout = statement_timeout_seconds
        self._batch_rows = batch_rows
        self._authorizer = authorizer
        self._maximum_rows = maximum_rows

    async def execute(
        self,
        run: RefreshRun,
        cancellation: asyncio.Event,
    ) -> RefreshExecutionResult:
        try:
            _raise_if_cancelled(cancellation)
            context = await self._context(run)
            if context is None:
                return RefreshExecutionResult(False, "draft_changed")
            _raise_if_cancelled(cancellation)
            artifact = await self._write(run, context, cancellation)
            return RefreshExecutionResult(True, snapshot=artifact)
        except RefreshCancelledError:
            return RefreshExecutionResult(False, cancelled=True)
        except RefreshRowLimitExceededError:
            return RefreshExecutionResult(False, "result_row_limit_exceeded")
        except UnsupportedSourceTypeError:
            return RefreshExecutionResult(False, "unsupported_source_type")
        except ConnectionSecretUnavailableError:
            return RefreshExecutionResult(False, "connection_secret_unavailable")
        except ConnectionAccessDeniedError:
            return RefreshExecutionResult(False, "connection_access_revoked")
        except (TimeoutError, psycopg.Error, adbc_dbapi.Error):
            return RefreshExecutionResult(False, "source_unavailable", retryable=True)
        except ObjectStorageUnavailableError:
            return RefreshExecutionResult(
                False,
                "object_storage_unavailable",
                retryable=True,
            )
        except (
            OSError,
            pa.ArrowException,
            ValueError,
            TypeError,
        ):
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
        cancellation: asyncio.Event,
    ) -> SnapshotArtifact:
        snapshot_id = uuid4()
        object_key = f"datasets/{run.dataset_id}/snapshots/{snapshot_id}.parquet"
        staging_path = self._work_root / ".staging" / f"{run.id}-{snapshot_id}.parquet"
        staging_path.parent.mkdir(parents=True, exist_ok=True)
        expected_fields = tuple((field.id, field.name) for field in context.definition.fields)
        validated_fields = tuple(
            (field.field_id, field.name) for field in context.validation.result_schema
        )
        if validated_fields != expected_fields:
            raise ValueError("Validated result schema no longer matches definition")
        schema, adapters = arrow_schema(context.validation)
        row_count = 0
        try:
            _raise_if_cancelled(cancellation)
            query = compile_dataset_select(context.definition).as_string()
            thread_cancellation = threading.Event()
            cancel_handle = _AdbcCancelHandle()
            stream_task = asyncio.create_task(
                asyncio.to_thread(
                    _stream_arrow_snapshot,
                    staging_path,
                    connection=context.connection,
                    password=context.password,
                    ca_certificate=context.ca_certificate,
                    connect_timeout_seconds=self._connect_timeout,
                    statement_timeout_seconds=self._statement_timeout,
                    batch_rows=self._batch_rows,
                    maximum_rows=self._maximum_rows,
                    query=query,
                    schema=schema,
                    adapters=adapters,
                    cancellation=thread_cancellation,
                    cancel_handle=cancel_handle,
                )
            )
            cancellation_task = asyncio.create_task(cancellation.wait())
            try:
                done, _ = await asyncio.wait(
                    {stream_task, cancellation_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if cancellation_task in done and cancellation.is_set():
                    thread_cancellation.set()
                    await asyncio.to_thread(cancel_handle.cancel)
                    with suppress(adbc_dbapi.Error, RefreshCancelledError):
                        await stream_task
                    raise RefreshCancelledError
                row_count = await stream_task
            except asyncio.CancelledError:
                thread_cancellation.set()
                await asyncio.to_thread(cancel_handle.cancel)
                with suppress(adbc_dbapi.Error, RefreshCancelledError):
                    await asyncio.shield(stream_task)
                raise
            finally:
                cancellation_task.cancel()
                with suppress(asyncio.CancelledError):
                    await cancellation_task
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
            checksum_sha256 = checksum.hexdigest()
            _raise_if_cancelled(cancellation)
            await asyncio.to_thread(
                self._storage.publish_candidate,
                staging_path,
                object_key,
                byte_size=byte_size,
                checksum_sha256=checksum_sha256,
                content_type="application/vnd.apache.parquet",
            )
            return SnapshotArtifact(
                id=snapshot_id,
                storage_backend=self._storage.backend,
                object_key=object_key,
                row_count=row_count,
                byte_size=byte_size,
                checksum_sha256=checksum_sha256,
                schema_document=_schema_document(adapters),
            )
        finally:
            staging_path.unlink(missing_ok=True)


class LocalParquetSnapshotExecutor(ParquetSnapshotExecutor):
    """Compatibility adapter for local development and focused tests."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        cipher: AesGcmSecretCipher,
        storage_root: Path,
        connect_timeout_seconds: int,
        statement_timeout_seconds: int,
        batch_rows: int,
        authorizer: ConnectionAccessAuthorizer,
        maximum_rows: int = 10_000_000,
    ) -> None:
        super().__init__(
            session_factory,
            cipher,
            LocalObjectStorage(storage_root),
            storage_root,
            connect_timeout_seconds,
            statement_timeout_seconds,
            batch_rows,
            authorizer,
            maximum_rows,
        )
