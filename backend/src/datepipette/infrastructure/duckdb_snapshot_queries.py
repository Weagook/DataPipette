"""Bounded DuckDB queries over private Parquet snapshot objects."""

import asyncio
import base64
import json
import math
from contextlib import suppress
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from datepipette.application.snapshot_queries import (
    DatasetSnapshotQueryTimeoutError,
    DatasetSnapshotResultTooLargeError,
    DatasetSnapshotUnavailableError,
)
from datepipette.domain.snapshot_queries import (
    ActiveSnapshotQueryContext,
    SnapshotFilterOperator,
    SnapshotQueryCursorPosition,
    SnapshotQueryExecution,
    SnapshotQueryField,
    SnapshotQueryFilter,
    SnapshotQuerySort,
    SnapshotSortDirection,
)
from datepipette.infrastructure.object_storage import (
    LocalObjectStorage,
    ObjectStorageRegistry,
    ObjectStorageUnavailableError,
)


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _column(field: SnapshotQueryField) -> str:
    return f"source.{_quote_identifier(field.storage_name)}"


def _response_value(field: SnapshotQueryField, value: Any) -> Any:
    if isinstance(value, str) and field.data_type.lower() in {"json", "jsonb"}:
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise DatasetSnapshotUnavailableError from exc
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat() if value.tzinfo is not None else value.isoformat()
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return base64.b64encode(bytes(value)).decode("ascii")
    return str(value)


def _compile_filters(
    filters: tuple[SnapshotQueryFilter, ...],
    fields: dict[Any, SnapshotQueryField],
) -> tuple[list[str], list[Any]]:
    clauses: list[str] = []
    parameters: list[Any] = []
    comparisons = {
        SnapshotFilterOperator.EQUALS: "=",
        SnapshotFilterOperator.NOT_EQUALS: "<>",
        SnapshotFilterOperator.GREATER_THAN: ">",
        SnapshotFilterOperator.GREATER_THAN_OR_EQUAL: ">=",
        SnapshotFilterOperator.LESS_THAN: "<",
        SnapshotFilterOperator.LESS_THAN_OR_EQUAL: "<=",
    }
    for item in filters:
        column = _column(fields[item.field_id])
        if item.operator in comparisons:
            clauses.append(f"{column} {comparisons[item.operator]} ?")
            parameters.append(item.value)
        elif item.operator is SnapshotFilterOperator.BETWEEN:
            clauses.append(f"({column} >= ? AND {column} <= ?)")
            parameters.extend(item.values)
        elif item.operator is SnapshotFilterOperator.IN:
            placeholders = ", ".join("?" for _ in item.values)
            clauses.append(f"{column} IN ({placeholders})")
            parameters.extend(item.values)
        elif item.operator is SnapshotFilterOperator.CONTAINS:
            clauses.append(f"contains({column}, ?)")
            parameters.append(item.value)
        elif item.operator is SnapshotFilterOperator.STARTS_WITH:
            clauses.append(f"starts_with({column}, ?)")
            parameters.append(item.value)
        elif item.operator is SnapshotFilterOperator.IS_NULL:
            clauses.append(f"{column} IS NULL")
        elif item.operator is SnapshotFilterOperator.IS_NOT_NULL:
            clauses.append(f"{column} IS NOT NULL")
    return clauses, parameters


def _equal_clause(column: str, value: Any, parameters: list[Any]) -> str:
    if value is None:
        return f"{column} IS NULL"
    parameters.append(value)
    return f"{column} = ?"


def _after_clause(
    column: str,
    value: Any,
    direction: SnapshotSortDirection,
    parameters: list[Any],
) -> str | None:
    if value is None:
        return None
    operator = ">" if direction is SnapshotSortDirection.ASCENDING else "<"
    parameters.append(value)
    return f"({column} {operator} ? OR {column} IS NULL)"


def _compile_position(
    sorts: tuple[SnapshotQuerySort, ...],
    fields: dict[Any, SnapshotQueryField],
    position: SnapshotQueryCursorPosition,
) -> tuple[str, list[Any]]:
    parameters: list[Any] = []
    branches: list[str] = []
    for index, (item, value) in enumerate(zip(sorts, position.sort_values, strict=True)):
        branch_parameters: list[Any] = []
        equal_prefix = [
            _equal_clause(
                _column(fields[previous.field_id]),
                position.sort_values[previous_index],
                branch_parameters,
            )
            for previous_index, previous in enumerate(sorts[:index])
        ]
        column = _column(fields[item.field_id])
        after = _after_clause(column, value, item.direction, branch_parameters)
        if after is not None:
            branches.append("(" + " AND ".join((*equal_prefix, after)) + ")")
            parameters.extend(branch_parameters)
    final_parameters: list[Any] = []
    equal_prefix = [
        _equal_clause(
            _column(fields[item.field_id]),
            position.sort_values[index],
            final_parameters,
        )
        for index, item in enumerate(sorts)
    ]
    final_parameters.append(position.file_row_number)
    branches.append("(" + " AND ".join((*equal_prefix, "source.file_row_number > ?")) + ")")
    parameters.extend(final_parameters)
    return "(" + " OR ".join(branches) + ")", parameters


def _compile_query(
    *,
    selected_fields: tuple[SnapshotQueryField, ...],
    all_fields: tuple[SnapshotQueryField, ...],
    filters: tuple[SnapshotQueryFilter, ...],
    sorts: tuple[SnapshotQuerySort, ...],
    position: SnapshotQueryCursorPosition | None,
) -> tuple[str, list[Any]]:
    fields = {field.field_id: field for field in all_fields}
    projections = [
        f"{_column(field)} AS {_quote_identifier(field.name)}" for field in selected_fields
    ]
    projections.extend(
        f"{_column(fields[item.field_id])} AS {_quote_identifier(f'__dp_sort_{index}')}"
        for index, item in enumerate(sorts)
    )
    projections.append('source.file_row_number AS "__dp_file_row_number"')
    clauses, parameters = _compile_filters(filters, fields)
    if position is not None:
        position_clause, position_parameters = _compile_position(sorts, fields, position)
        clauses.append(position_clause)
        parameters.extend(position_parameters)
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


class DuckDbSnapshotQueryExecutor:
    def __init__(
        self,
        storages: ObjectStorageRegistry,
        work_root: Path,
        *,
        timeout_seconds: float,
        memory_limit_mb: int,
        threads: int,
        temporary_directory_limit_mb: int,
        maximum_concurrent_queries: int,
    ) -> None:
        self._storages = storages
        self._work_root = work_root.resolve()
        self._timeout_seconds = timeout_seconds
        self._memory_limit_mb = memory_limit_mb
        self._threads = threads
        self._temporary_directory_limit_mb = temporary_directory_limit_mb
        self._semaphore = asyncio.Semaphore(maximum_concurrent_queries)

    async def query(
        self,
        *,
        context: ActiveSnapshotQueryContext,
        selected_fields: tuple[SnapshotQueryField, ...],
        filters: tuple[SnapshotQueryFilter, ...],
        sorts: tuple[SnapshotQuerySort, ...],
        position: SnapshotQueryCursorPosition | None,
        page_size: int,
        maximum_bytes: int,
    ) -> SnapshotQueryExecution:
        try:
            await asyncio.wait_for(self._semaphore.acquire(), self._timeout_seconds)
        except TimeoutError as exc:
            raise DatasetSnapshotQueryTimeoutError from exc
        try:
            return await self._query_once(
                context=context,
                selected_fields=selected_fields,
                filters=filters,
                sorts=sorts,
                position=position,
                page_size=page_size,
                maximum_bytes=maximum_bytes,
            )
        finally:
            self._semaphore.release()

    async def _query_once(
        self,
        *,
        context: ActiveSnapshotQueryContext,
        selected_fields: tuple[SnapshotQueryField, ...],
        filters: tuple[SnapshotQueryFilter, ...],
        sorts: tuple[SnapshotQuerySort, ...],
        position: SnapshotQueryCursorPosition | None,
        page_size: int,
        maximum_bytes: int,
    ) -> SnapshotQueryExecution:
        statement, parameters = _compile_query(
            selected_fields=selected_fields,
            all_fields=context.fields,
            filters=filters,
            sorts=sorts,
            position=position,
        )
        try:
            materialized = self._storages.require(context.storage_backend).materialize(
                context.object_key,
                expected_byte_size=context.byte_size,
                expected_checksum_sha256=context.checksum_sha256,
            )
            path = await asyncio.to_thread(materialized.__enter__)
        except (OSError, ObjectStorageUnavailableError, ValueError) as exc:
            raise DatasetSnapshotUnavailableError from exc
        temporary_directory = self._work_root / ".query-tmp"
        try:
            temporary_directory.mkdir(parents=True, exist_ok=True)
            connection = duckdb.connect(
                ":memory:",
                config={
                    "threads": str(self._threads),
                    "memory_limit": f"{self._memory_limit_mb}MB",
                    "temp_directory": str(temporary_directory),
                    "max_temp_directory_size": f"{self._temporary_directory_limit_mb}MB",
                    "autoinstall_known_extensions": "false",
                    "autoload_known_extensions": "false",
                },
            )
        except (duckdb.Error, OSError) as exc:
            await asyncio.to_thread(materialized.__exit__, None, None, None)
            raise DatasetSnapshotUnavailableError from exc
        task = asyncio.create_task(
            asyncio.to_thread(
                self._execute,
                connection,
                statement,
                [str(path), *parameters, page_size + 1],
                selected_fields,
                sorts,
                page_size,
                maximum_bytes,
            )
        )
        try:
            return await asyncio.wait_for(asyncio.shield(task), self._timeout_seconds)
        except TimeoutError as exc:
            connection.interrupt()
            with suppress(Exception):
                await asyncio.wait_for(asyncio.shield(task), 5)
            raise DatasetSnapshotQueryTimeoutError from exc
        except (duckdb.Error, OSError, ValueError, TypeError) as exc:
            raise DatasetSnapshotUnavailableError from exc
        finally:
            if task.done():
                connection.close()
                await asyncio.to_thread(materialized.__exit__, None, None, None)
            else:

                def cleanup(_: asyncio.Task[SnapshotQueryExecution]) -> None:
                    connection.close()
                    with suppress(Exception):
                        materialized.__exit__(None, None, None)

                task.add_done_callback(cleanup)

    @staticmethod
    def _execute(
        connection: duckdb.DuckDBPyConnection,
        statement: str,
        parameters: list[Any],
        selected_fields: tuple[SnapshotQueryField, ...],
        sorts: tuple[SnapshotQuerySort, ...],
        page_size: int,
        maximum_bytes: int,
    ) -> SnapshotQueryExecution:
        records = connection.execute(statement, parameters).fetchmany(page_size + 1)
        rows: list[dict[str, Any]] = []
        positions: list[SnapshotQueryCursorPosition] = []
        encoded_bytes = 0
        truncated_by_bytes = False
        for record in records[:page_size]:
            row = {
                field.name: _response_value(field, record[index])
                for index, field in enumerate(selected_fields)
            }
            row_bytes = len(
                json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            )
            if encoded_bytes + row_bytes > maximum_bytes:
                if not rows:
                    raise DatasetSnapshotResultTooLargeError
                truncated_by_bytes = True
                break
            rows.append(row)
            encoded_bytes += row_bytes
            sort_start = len(selected_fields)
            positions.append(
                SnapshotQueryCursorPosition(
                    sort_values=tuple(record[sort_start + index] for index in range(len(sorts))),
                    file_row_number=record[sort_start + len(sorts)],
                )
            )
        has_more = truncated_by_bytes or len(records) > page_size
        next_position = positions[-1] if has_more and positions else None
        return SnapshotQueryExecution(
            rows=tuple(rows),
            next_position=next_position,
            truncated_by_bytes=truncated_by_bytes,
        )


class DuckDbLocalSnapshotQueryExecutor(DuckDbSnapshotQueryExecutor):
    """Compatibility adapter for local development and focused tests."""

    def __init__(
        self,
        storage_root: Path,
        *,
        timeout_seconds: float,
        memory_limit_mb: int,
        threads: int,
        temporary_directory_limit_mb: int,
        maximum_concurrent_queries: int,
    ) -> None:
        local = LocalObjectStorage(storage_root)
        super().__init__(
            ObjectStorageRegistry(local.backend, {local.backend: local}),
            storage_root,
            timeout_seconds=timeout_seconds,
            memory_limit_mb=memory_limit_mb,
            threads=threads,
            temporary_directory_limit_mb=temporary_directory_limit_mb,
            maximum_concurrent_queries=maximum_concurrent_queries,
        )
