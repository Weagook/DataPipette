"""Streaming CSV/XLSX generation into private analytical object storage."""

import asyncio
import base64
import csv
import hashlib
import json
from collections.abc import Iterator
from contextlib import suppress
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path
from threading import Event, Timer
from time import monotonic
from typing import Any

import duckdb
from openpyxl import Workbook  # type: ignore[import-untyped]

from datepipette.application.exports import (
    ExportGenerationTimeoutError,
    ExportLimitExceededError,
    export_query_from_document,
)
from datepipette.application.snapshot_queries import prepare_snapshot_query
from datepipette.domain.exports import ExportArtifact, ExportFormat, ExportJob
from datepipette.domain.snapshot_queries import ActiveSnapshotQueryContext, SnapshotQueryField
from datepipette.infrastructure.duckdb_snapshot_queries import (
    _column,
    compile_snapshot_filters,
    quote_identifier,
)
from datepipette.infrastructure.object_storage import (
    LocalObjectStorage,
    ObjectStorageRegistry,
    ObjectStorageUnavailableError,
)

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_XLSX_MAX_DATA_ROWS = 1_048_575


def _safe_string(value: str) -> str:
    return "'" + value if value.startswith(_FORMULA_PREFIXES) else value


def _export_value(field: SnapshotQueryField, value: Any, *, csv_mode: bool) -> Any:
    if value is None:
        return "" if csv_mode else None
    if isinstance(value, str):
        if field.data_type.lower() in {"json", "jsonb"}:
            with suppress(json.JSONDecodeError):
                value = json.dumps(
                    json.loads(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True
                )
        return _safe_string(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return base64.b64encode(bytes(value)).decode("ascii")
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(UTC)
            return value.isoformat() if csv_mode else value.replace(tzinfo=None)
        return value.isoformat() if csv_mode else value
    if isinstance(value, (date, time)):
        return value.isoformat() if csv_mode else value
    return value


class LocalExportStorage:
    def __init__(self, storage_root: Path) -> None:
        self._storage = LocalObjectStorage(storage_root)
        self.root = self._storage.root

    def resolve(self, object_key: str) -> Path:
        try:
            return self._storage.materialize_path(object_key)
        except ObjectStorageUnavailableError as exc:
            raise ValueError("Unavailable export object") from exc


class DatasetExportExecutor:
    def __init__(
        self,
        storages: ObjectStorageRegistry,
        work_root: Path,
        *,
        memory_limit_mb: int,
        threads: int,
        temporary_directory_limit_mb: int,
        maximum_rows: int,
        fetch_rows: int,
        maximum_bytes: int = 1_073_741_824,
        timeout_seconds: int = 1800,
    ) -> None:
        self._storages = storages
        self._storage = storages.default
        self._work_root = work_root.resolve()
        self._memory_limit_mb = memory_limit_mb
        self._threads = threads
        self._temporary_directory_limit_mb = temporary_directory_limit_mb
        self._maximum_rows = maximum_rows
        self._maximum_bytes = maximum_bytes
        self._timeout_seconds = timeout_seconds
        self._fetch_rows = fetch_rows

    async def execute(self, job: ExportJob, context: ActiveSnapshotQueryContext) -> ExportArtifact:
        return await asyncio.to_thread(self._execute, job, context)

    def _execute(self, job: ExportJob, context: ActiveSnapshotQueryContext) -> ExportArtifact:
        query = export_query_from_document(
            {
                "fields": [str(value) for value in job.query.field_ids],
                "filters": [
                    {
                        "field_id": str(item.field_id),
                        "operator": item.operator.value,
                        "value": item.value,
                        "values": list(item.values),
                    }
                    for item in job.query.filters
                ],
                "sorts": [
                    {"field_id": str(item.field_id), "direction": item.direction.value}
                    for item in job.query.sorts
                ],
            }
        )
        prepared = prepare_snapshot_query(context, query, require_page=False)
        suffix = job.format.value
        object_key = f"exports/{job.dataset_id}/{job.id}.{suffix}"
        temporary = self._work_root / ".export-tmp"
        temporary.mkdir(parents=True, exist_ok=True)
        candidate = temporary / f"{job.id}.{suffix}.part"
        connection = duckdb.connect(
            ":memory:",
            config={
                "threads": str(self._threads),
                "memory_limit": f"{self._memory_limit_mb}MB",
                "temp_directory": str(temporary),
                "max_temp_directory_size": f"{self._temporary_directory_limit_mb}MB",
                "autoinstall_known_extensions": "false",
                "autoload_known_extensions": "false",
            },
        )
        timed_out = Event()

        def interrupt() -> None:
            timed_out.set()
            with suppress(Exception):
                connection.interrupt()

        deadline = monotonic() + self._timeout_seconds
        timer = Timer(self._timeout_seconds, interrupt)
        timer.daemon = True
        timer.start()
        try:
            with self._storages.require(context.storage_backend).materialize(
                context.object_key,
                expected_byte_size=context.byte_size,
                expected_checksum_sha256=context.checksum_sha256,
            ) as source:
                self._raise_if_timed_out(deadline, timed_out)
                statement, parameters = self._statement(
                    context, prepared.selected_fields, prepared.filters, prepared.sorts
                )
                cursor = connection.execute(
                    statement, [str(source), *parameters, self._maximum_rows + 1]
                )
                if job.format is ExportFormat.CSV:
                    row_count = self._write_csv(
                        candidate,
                        cursor,
                        prepared.selected_fields,
                        deadline,
                        timed_out,
                    )
                    content_type = "text/csv; charset=utf-8"
                else:
                    row_count = self._write_xlsx(
                        candidate,
                        cursor,
                        prepared.selected_fields,
                        deadline,
                        timed_out,
                    )
                    content_type = (
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )
                self._raise_if_timed_out(deadline, timed_out)
        except Exception as exc:
            candidate.unlink(missing_ok=True)
            if timed_out.is_set() and not isinstance(exc, ExportGenerationTimeoutError):
                raise ExportGenerationTimeoutError from exc
            raise
        finally:
            timer.cancel()
            connection.close()
        try:
            self._raise_if_timed_out(deadline, timed_out)
            byte_size = candidate.stat().st_size
            if byte_size > self._maximum_bytes:
                raise ExportLimitExceededError("Export artifact byte limit exceeded")
            checksum = self._checksum(candidate)
            self._storage.publish_candidate(
                candidate,
                object_key,
                byte_size=byte_size,
                checksum_sha256=checksum,
                content_type=content_type,
            )
            return ExportArtifact(
                object_key,
                row_count,
                byte_size,
                checksum,
                content_type,
                self._storage.backend,
            )
        finally:
            candidate.unlink(missing_ok=True)

    def _statement(
        self,
        context: ActiveSnapshotQueryContext,
        selected: tuple[SnapshotQueryField, ...],
        filters: tuple[Any, ...],
        sorts: tuple[Any, ...],
    ) -> tuple[str, list[Any]]:
        fields = {field.field_id: field for field in context.fields}
        projections = [f"{_column(field)} AS {quote_identifier(field.name)}" for field in selected]
        clauses, parameters = compile_snapshot_filters(filters, fields)
        order = [
            f"{_column(fields[item.field_id])} {item.direction.value.upper()} NULLS LAST"
            for item in sorts
        ]
        order.append("source.file_row_number ASC")
        statement = (
            "SELECT "
            + ", ".join(projections)
            + " FROM read_parquet(?, file_row_number = true) AS source"
            + (" WHERE " + " AND ".join(f"({clause})" for clause in clauses) if clauses else "")
            + " ORDER BY "
            + ", ".join(order)
            + " LIMIT ?"
        )
        return statement, parameters

    def _batches(
        self,
        cursor: duckdb.DuckDBPyConnection,
        deadline: float,
        timed_out: Event,
    ) -> Iterator[tuple[Any, ...]]:
        total = 0
        while True:
            self._raise_if_timed_out(deadline, timed_out)
            rows = cursor.fetchmany(self._fetch_rows)
            if not rows:
                break
            for row in rows:
                total += 1
                if total > self._maximum_rows:
                    raise ExportLimitExceededError("Export row limit exceeded")
                yield row

    def _write_csv(
        self,
        path: Path,
        cursor: duckdb.DuckDBPyConnection,
        fields: tuple[SnapshotQueryField, ...],
        deadline: float,
        timed_out: Event,
    ) -> int:
        count = 0
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream, lineterminator="\n")
            writer.writerow([_safe_string(field.name) for field in fields])
            for record in self._batches(cursor, deadline, timed_out):
                writer.writerow(
                    [
                        _export_value(field, record[index], csv_mode=True)
                        for index, field in enumerate(fields)
                    ]
                )
                count += 1
        return count

    def _write_xlsx(
        self,
        path: Path,
        cursor: duckdb.DuckDBPyConnection,
        fields: tuple[SnapshotQueryField, ...],
        deadline: float,
        timed_out: Event,
    ) -> int:
        workbook = Workbook(write_only=True)
        sheet = workbook.create_sheet("Data")
        sheet.append([_safe_string(field.name) for field in fields])
        count = 0
        try:
            for record in self._batches(cursor, deadline, timed_out):
                if count >= _XLSX_MAX_DATA_ROWS:
                    raise ExportLimitExceededError("XLSX row limit exceeded")
                sheet.append(
                    [
                        _export_value(field, record[index], csv_mode=False)
                        for index, field in enumerate(fields)
                    ]
                )
                count += 1
            workbook.save(path)
            self._raise_if_timed_out(deadline, timed_out)
        finally:
            workbook.close()
        return count

    @staticmethod
    def _raise_if_timed_out(deadline: float, timed_out: Event) -> None:
        if timed_out.is_set() or monotonic() >= deadline:
            timed_out.set()
            raise ExportGenerationTimeoutError

    @staticmethod
    def _checksum(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()


class LocalDatasetExportExecutor(DatasetExportExecutor):
    """Compatibility adapter for local development and focused tests."""

    def __init__(
        self,
        storage_root: Path,
        *,
        memory_limit_mb: int,
        threads: int,
        temporary_directory_limit_mb: int,
        maximum_rows: int,
        fetch_rows: int,
        maximum_bytes: int = 1_073_741_824,
        timeout_seconds: int = 1800,
    ) -> None:
        local = LocalObjectStorage(storage_root)
        super().__init__(
            ObjectStorageRegistry(local.backend, {local.backend: local}),
            storage_root,
            memory_limit_mb=memory_limit_mb,
            threads=threads,
            temporary_directory_limit_mb=temporary_directory_limit_mb,
            maximum_rows=maximum_rows,
            maximum_bytes=maximum_bytes,
            timeout_seconds=timeout_seconds,
            fetch_rows=fetch_rows,
        )
