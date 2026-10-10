"""Streaming CSV/XLSX generation and private local export storage."""

import asyncio
import base64
import csv
import hashlib
import json
import os
from collections.abc import Iterator
from contextlib import suppress
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
from openpyxl import Workbook  # type: ignore[import-untyped]

from datepipette.application.exports import export_query_from_document
from datepipette.application.snapshot_queries import prepare_snapshot_query
from datepipette.domain.exports import ExportArtifact, ExportFormat, ExportJob
from datepipette.domain.snapshot_queries import ActiveSnapshotQueryContext, SnapshotQueryField
from datepipette.infrastructure.duckdb_snapshot_queries import (
    _column,
    _compile_filters,
    _quote_identifier,
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
        self.root = storage_root.resolve()

    def resolve(self, object_key: str) -> Path:
        if Path(object_key).is_absolute():
            raise ValueError("Absolute export key")
        path = (self.root / object_key).resolve()
        if not path.is_relative_to(self.root) or not path.is_file():
            raise ValueError("Unavailable export object")
        return path


class LocalDatasetExportExecutor:
    def __init__(
        self,
        storage_root: Path,
        *,
        memory_limit_mb: int,
        threads: int,
        temporary_directory_limit_mb: int,
        maximum_rows: int,
        fetch_rows: int,
    ) -> None:
        self._storage = LocalExportStorage(storage_root)
        self._memory_limit_mb = memory_limit_mb
        self._threads = threads
        self._temporary_directory_limit_mb = temporary_directory_limit_mb
        self._maximum_rows = maximum_rows
        self._fetch_rows = fetch_rows

    async def execute(self, job: ExportJob, context: ActiveSnapshotQueryContext) -> ExportArtifact:
        return await asyncio.to_thread(self._execute, job, context)

    def _snapshot_path(self, context: ActiveSnapshotQueryContext) -> Path:
        if context.storage_backend != "local" or Path(context.object_key).is_absolute():
            raise ValueError("Unsupported snapshot storage")
        path = (self._storage.root / context.object_key).resolve()
        if not path.is_relative_to(self._storage.root) or not path.is_file():
            raise ValueError("Snapshot unavailable")
        return path

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
        source = self._snapshot_path(context)
        suffix = job.format.value
        object_key = f"exports/{job.dataset_id}/{job.id}.{suffix}"
        target = self._storage.root / object_key
        target.parent.mkdir(parents=True, exist_ok=True)
        candidate = target.with_suffix(target.suffix + ".part")
        temporary = self._storage.root / ".export-tmp"
        temporary.mkdir(parents=True, exist_ok=True)
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
        try:
            statement, parameters = self._statement(
                context, prepared.selected_fields, prepared.filters, prepared.sorts
            )
            cursor = connection.execute(
                statement, [str(source), *parameters, self._maximum_rows + 1]
            )
            if job.format is ExportFormat.CSV:
                row_count = self._write_csv(candidate, cursor, prepared.selected_fields)
                content_type = "text/csv; charset=utf-8"
            else:
                row_count = self._write_xlsx(candidate, cursor, prepared.selected_fields)
                content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            os.replace(candidate, target)
        finally:
            connection.close()
            candidate.unlink(missing_ok=True)
        byte_size = target.stat().st_size
        checksum = self._checksum(target)
        return ExportArtifact(object_key, row_count, byte_size, checksum, content_type)

    def _statement(
        self,
        context: ActiveSnapshotQueryContext,
        selected: tuple[SnapshotQueryField, ...],
        filters: tuple[Any, ...],
        sorts: tuple[Any, ...],
    ) -> tuple[str, list[Any]]:
        fields = {field.field_id: field for field in context.fields}
        projections = [f"{_column(field)} AS {_quote_identifier(field.name)}" for field in selected]
        clauses, parameters = _compile_filters(filters, fields)
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

    def _batches(self, cursor: duckdb.DuckDBPyConnection) -> Iterator[tuple[Any, ...]]:
        total = 0
        while rows := cursor.fetchmany(self._fetch_rows):
            for row in rows:
                total += 1
                if total > self._maximum_rows:
                    raise ValueError("Export row limit exceeded")
                yield row

    def _write_csv(
        self, path: Path, cursor: duckdb.DuckDBPyConnection, fields: tuple[SnapshotQueryField, ...]
    ) -> int:
        count = 0
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream, lineterminator="\n")
            writer.writerow([_safe_string(field.name) for field in fields])
            for record in self._batches(cursor):
                writer.writerow(
                    [
                        _export_value(field, record[index], csv_mode=True)
                        for index, field in enumerate(fields)
                    ]
                )
                count += 1
        return count

    def _write_xlsx(
        self, path: Path, cursor: duckdb.DuckDBPyConnection, fields: tuple[SnapshotQueryField, ...]
    ) -> int:
        workbook = Workbook(write_only=True)
        sheet = workbook.create_sheet("Data")
        sheet.append([_safe_string(field.name) for field in fields])
        count = 0
        try:
            for record in self._batches(cursor):
                if count >= _XLSX_MAX_DATA_ROWS:
                    raise ValueError("XLSX row limit exceeded")
                sheet.append(
                    [
                        _export_value(field, record[index], csv_mode=False)
                        for index, field in enumerate(fields)
                    ]
                )
                count += 1
            workbook.save(path)
        finally:
            workbook.close()
        return count

    @staticmethod
    def _checksum(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()
