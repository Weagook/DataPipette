"""Bounded source streaming into immutable Parquet snapshot objects."""

import asyncio
import hashlib
import io
import json
import os
import queue
import re
import threading
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.csv as pacsv  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from psycopg import sql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from datapipette.application.connection_access import (
    ConnectionAccessAuthorizer,
    ConnectionAccessDeniedError,
)
from datapipette.application.connections import ConnectionSecretUnavailableError
from datapipette.application.refresh_worker import RefreshExecutionResult
from datapipette.domain.connections import PostgresConnection
from datapipette.domain.datasets import (
    DatasetDefinition,
    DatasetValidation,
    DatasetValidationState,
    dataset_definition_from_document,
    definition_relations,
)
from datapipette.domain.refresh import RefreshRun
from datapipette.domain.snapshots import SnapshotArtifact
from datapipette.infrastructure.crypto import AesGcmSecretCipher
from datapipette.infrastructure.dataset_sql import compile_dataset_select
from datapipette.infrastructure.models import (
    DatasetModel,
    DatasetVersionModel,
    PostgresConnectionModel,
    UserModel,
)
from datapipette.infrastructure.object_storage import (
    LocalObjectStorage,
    ObjectStorage,
    ObjectStorageUnavailableError,
)
from datapipette.infrastructure.repositories.authentication import to_user
from datapipette.infrastructure.repositories.connections import to_connection
from datapipette.infrastructure.source_postgres import open_read_only_source

# Session settings that make PostgreSQL's CSV text round-trip exactly into
# Arrow: UTC offsets, ISO dates, and shortest-exact floating-point output.
_CSV_SESSION_SETTINGS = (
    ("TimeZone", "UTC"),
    ("DateStyle", "ISO, YMD"),
    ("extra_float_digits", "3"),
)

_TIMESTAMP = re.compile(r"^timestamp(?:\([0-6]\))? (with|without) time zone$")
_TIME = re.compile(r"^time(?:\([0-6]\))? (with|without) time zone$")
_NUMERIC = re.compile(r"^(?:numeric|decimal)\(([0-9]+),(-?[0-9]+)\)$")
_CHARACTER = re.compile(r"^(?:character varying|varchar|character|char)(?:\([0-9]+\))?$")
_CSV_NATIVE_TYPES: dict[str, pa.DataType] = {
    "boolean": pa.bool_(),
    "smallint": pa.int16(),
    "integer": pa.int32(),
    "bigint": pa.int64(),
    "real": pa.float32(),
    "double precision": pa.float64(),
    "date": pa.date32(),
}
_CSV_CHUNK_BYTES = 4 * 1024 * 1024
_CSV_BLOCK_BYTES = 16 * 1024 * 1024
_CSV_QUEUE_CHUNKS = 8


class UnsupportedSourceTypeError(ValueError):
    pass


class RefreshCancelledError(Exception):
    pass


class RefreshRowLimitExceededError(Exception):
    pass


def _raise_if_cancelled(cancellation: asyncio.Event) -> None:
    if cancellation.is_set():
        raise RefreshCancelledError


async def _cancel_source_when_requested(
    source: psycopg.AsyncConnection[Any],
    cancellation: asyncio.Event,
) -> None:
    await cancellation.wait()
    await source.cancel_safe()


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
    # PostgreSQL's CSV text for this type parses in Arrow to the same value the
    # row path would store, so the column can use the columnar COPY stream.
    csv_compatible: bool = False


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
    csv_compatible = False
    if normalized in _CSV_NATIVE_TYPES:
        arrow_type = _CSV_NATIVE_TYPES[normalized]
        csv_compatible = True
    elif match := _NUMERIC.fullmatch(normalized):
        precision, scale = int(match.group(1)), int(match.group(2))
        if precision <= 38:
            arrow_type = pa.decimal128(precision, scale)
            # Arrow parses CSV decimals only into 128-bit, non-negative scales.
            csv_compatible = scale >= 0
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
        # PostgreSQL's canonical UUID text is exactly Python's str(UUID).
        csv_compatible = normalized == "uuid"
    elif normalized in {"text", "name", "citext"} or _CHARACTER.fullmatch(normalized):
        arrow_type = pa.string()
        csv_compatible = True
    elif timestamp_match := _TIMESTAMP.fullmatch(normalized):
        arrow_type = (
            pa.timestamp("us", tz="UTC")
            if timestamp_match.group(1) == "with"
            else pa.timestamp("us")
        )
        csv_compatible = True
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
        csv_compatible,
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
            metadata={b"datapipette:postgres_type": item.postgres_type.encode()},
        )
        for item in adapters
    ]
    return (
        pa.schema(fields, metadata={b"datapipette:schema_version": b"2"}),
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


def _append_rows(
    writer: pq.ParquetWriter,
    rows: Sequence[Sequence[Any]],
    *,
    schema: pa.Schema,
    adapters: tuple[_ColumnAdapter, ...],
) -> int:
    batch = _record_batch_from_rows(rows, schema=schema, adapters=adapters)
    writer.write_batch(batch)
    return int(batch.num_rows)


class _CsvStreamRejectedError(Exception):
    """Arrow could not parse the CSV stream; the row path must produce the snapshot."""


class _CsvConsumerStoppedError(Exception):
    """The CSV consumer finished early and stopped accepting chunks."""


_CsvChunk = bytes | None


class _CsvChunkReader(io.RawIOBase):
    """Expose queued COPY chunks to Arrow's blocking CSV reader."""

    def __init__(self, chunks: "queue.Queue[_CsvChunk]", producer_failed: threading.Event) -> None:
        self._chunks = chunks
        self._producer_failed = producer_failed
        self._pending = memoryview(b"")
        self._finished = False

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        while not self._pending:
            if self._finished:
                return 0
            try:
                chunk = self._chunks.get(timeout=0.1)
            except queue.Empty:
                if self._producer_failed.is_set():
                    raise _CsvConsumerStoppedError from None
                continue
            if chunk is None:
                self._finished = True
                return 0
            self._pending = memoryview(chunk)
        size = min(len(buffer), len(self._pending))
        buffer[:size] = self._pending[:size]
        self._pending = self._pending[size:]
        return size


def _write_csv_stream(
    writer: pq.ParquetWriter,
    chunks: "queue.Queue[_CsvChunk]",
    *,
    schema: pa.Schema,
    adapters: tuple[_ColumnAdapter, ...],
    maximum_rows: int,
    consumer_stopped: threading.Event,
    producer_failed: threading.Event,
) -> int:
    """Parse PostgreSQL CSV into typed Arrow batches and append them to the snapshot."""

    row_count = 0
    try:
        try:
            reader = pacsv.open_csv(
                io.BufferedReader(
                    _CsvChunkReader(chunks, producer_failed),
                    buffer_size=_CSV_CHUNK_BYTES,
                ),
                read_options=pacsv.ReadOptions(
                    column_names=[adapter.storage_name for adapter in adapters],
                    block_size=_CSV_BLOCK_BYTES,
                ),
                parse_options=pacsv.ParseOptions(newlines_in_values=True),
                convert_options=pacsv.ConvertOptions(
                    column_types=schema,
                    true_values=["t"],
                    false_values=["f"],
                    null_values=[""],
                    strings_can_be_null=True,
                    quoted_strings_can_be_null=False,
                ),
            )
        except pa.ArrowInvalid as exc:
            # An empty result has no first block from which to infer anything.
            if "Empty CSV file" in str(exc):
                return 0
            raise _CsvStreamRejectedError from exc
        while True:
            try:
                parsed = reader.read_next_batch()
            except StopIteration:
                return row_count
            except (pa.ArrowInvalid, pa.ArrowNotImplementedError) as exc:
                raise _CsvStreamRejectedError from exc
            if row_count + parsed.num_rows > maximum_rows:
                raise RefreshRowLimitExceededError
            for adapter, column in zip(adapters, parsed.columns, strict=True):
                if not adapter.nullable and column.null_count:
                    raise ValueError("Non-nullable source field returned null")
            writer.write_batch(pa.RecordBatch.from_arrays(parsed.columns, schema=schema))
            row_count += parsed.num_rows
    finally:
        consumer_stopped.set()


def _put_csv_chunk(
    chunks: "queue.Queue[_CsvChunk]",
    chunk: _CsvChunk,
    consumer_stopped: threading.Event,
) -> bool:
    while not consumer_stopped.is_set():
        try:
            chunks.put(chunk, timeout=0.1)
            return True
        except queue.Full:
            continue
    return False


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
        except (TimeoutError, psycopg.Error):
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
        row_count: int | None = None
        writer: pq.ParquetWriter | None = None
        try:
            _raise_if_cancelled(cancellation)
            if all(adapter.csv_compatible for adapter in adapters):
                writer = _open_snapshot_writer(staging_path, schema)
                try:
                    row_count = await self._extract(
                        context,
                        cancellation,
                        self._csv_stream(writer, context, schema, adapters, cancellation),
                    )
                except _CsvStreamRejectedError:
                    writer.close()
                    writer = None
            if row_count is None:
                writer = _open_snapshot_writer(staging_path, schema)
                row_count = await self._extract(
                    context,
                    cancellation,
                    self._row_stream(writer, run, context, schema, adapters, cancellation),
                )
            assert writer is not None
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
            if writer is not None:
                writer.close()
            staging_path.unlink(missing_ok=True)

    async def _extract(
        self,
        context: _ExecutionContext,
        cancellation: asyncio.Event,
        stream: "Callable[[psycopg.AsyncConnection[Any]], Awaitable[int]]",
    ) -> int:
        async with open_read_only_source(
            context.connection,
            password=context.password,
            ca_certificate=context.ca_certificate,
            connect_timeout_seconds=self._connect_timeout,
            statement_timeout_seconds=self._statement_timeout,
        ) as source:
            source_cancellation = asyncio.create_task(
                _cancel_source_when_requested(source, cancellation)
            )
            try:
                return await stream(source)
            except psycopg.errors.QueryCanceled:
                _raise_if_cancelled(cancellation)
                raise
            finally:
                source_cancellation.cancel()
                with suppress(asyncio.CancelledError, psycopg.Error):
                    await source_cancellation

    def _row_stream(
        self,
        writer: pq.ParquetWriter,
        run: RefreshRun,
        context: _ExecutionContext,
        schema: pa.Schema,
        adapters: tuple[_ColumnAdapter, ...],
        cancellation: asyncio.Event,
    ) -> "Callable[[psycopg.AsyncConnection[Any]], Awaitable[int]]":
        async def stream(source: psycopg.AsyncConnection[Any]) -> int:
            row_count = 0
            cursor_name = f"datapipette_refresh_{run.id.hex}"
            async with source.cursor(name=cursor_name, binary=True) as cursor:
                _raise_if_cancelled(cancellation)
                await cursor.execute(compile_dataset_select(context.definition))
                rows = await cursor.fetchmany(self._batch_rows)
                while rows:
                    _raise_if_cancelled(cancellation)
                    if row_count + len(rows) > self._maximum_rows:
                        raise RefreshRowLimitExceededError
                    next_rows = asyncio.create_task(cursor.fetchmany(self._batch_rows))
                    try:
                        written_rows = await asyncio.to_thread(
                            _append_rows,
                            writer,
                            rows,
                            schema=schema,
                            adapters=adapters,
                        )
                        rows = await next_rows
                    except BaseException:
                        next_rows.cancel()
                        with suppress(asyncio.CancelledError, psycopg.Error):
                            await next_rows
                        raise
                    row_count += written_rows
                    _raise_if_cancelled(cancellation)
            return row_count

        return stream

    def _csv_stream(
        self,
        writer: pq.ParquetWriter,
        context: _ExecutionContext,
        schema: pa.Schema,
        adapters: tuple[_ColumnAdapter, ...],
        cancellation: asyncio.Event,
    ) -> "Callable[[psycopg.AsyncConnection[Any]], Awaitable[int]]":
        """Stream server-side CSV text into Arrow's multithreaded parser.

        PostgreSQL encodes wide rows far faster as text than in binary result
        format, and Arrow converts CSV columns natively without one Python
        object per cell.
        """

        async def stream(source: psycopg.AsyncConnection[Any]) -> int:
            chunks: queue.Queue[_CsvChunk] = queue.Queue(maxsize=_CSV_QUEUE_CHUNKS)
            consumer_stopped = threading.Event()
            producer_failed = threading.Event()

            async def put(chunk: _CsvChunk) -> None:
                if not await asyncio.to_thread(_put_csv_chunk, chunks, chunk, consumer_stopped):
                    raise _CsvConsumerStoppedError

            async with source.cursor() as cursor:
                for name, value in _CSV_SESSION_SETTINGS:
                    await cursor.execute("SELECT set_config(%s, %s, true)", (name, value))
                _raise_if_cancelled(cancellation)
                consumer = asyncio.create_task(
                    asyncio.to_thread(
                        _write_csv_stream,
                        writer,
                        chunks,
                        schema=schema,
                        adapters=adapters,
                        maximum_rows=self._maximum_rows,
                        consumer_stopped=consumer_stopped,
                        producer_failed=producer_failed,
                    )
                )
                try:
                    statement = sql.SQL("COPY ({}) TO STDOUT (FORMAT csv)").format(
                        compile_dataset_select(context.definition)
                    )
                    async with cursor.copy(statement) as copy:
                        pending = bytearray()
                        async for block in copy:
                            pending += block
                            if len(pending) >= _CSV_CHUNK_BYTES:
                                _raise_if_cancelled(cancellation)
                                await put(bytes(pending))
                                pending.clear()
                        if pending:
                            await put(bytes(pending))
                    await put(None)
                except _CsvConsumerStoppedError:
                    # The consumer stopped early; its own exception explains why.
                    return await consumer
                except BaseException:
                    producer_failed.set()
                    with suppress(BaseException):
                        await consumer
                    raise
                return await consumer

        return stream


def _open_snapshot_writer(path: Path, schema: pa.Schema) -> pq.ParquetWriter:
    return pq.ParquetWriter(
        path,
        schema,
        version="2.6",
        compression="zstd",
        use_dictionary=False,
        write_statistics=True,
        write_page_checksum=True,
    )


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
