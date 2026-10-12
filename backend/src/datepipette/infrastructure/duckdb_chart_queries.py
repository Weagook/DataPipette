"""Bounded aggregate chart queries over private Parquet snapshots."""

import asyncio
import json
from contextlib import suppress
from pathlib import Path
from typing import Any

import duckdb

from datepipette.application.charts import (
    ChartQueryResultTooLargeError,
    ChartQueryTimeoutError,
    ChartQueryUnavailableError,
    PreparedChartQuery,
)
from datepipette.domain.charts import (
    ChartAggregation,
    ChartQueryExecution,
    ChartResultColumn,
    ChartResultColumnRole,
    ChartSortTargetType,
)
from datepipette.domain.snapshot_queries import ActiveSnapshotQueryContext, SnapshotQueryField
from datepipette.infrastructure.duckdb_snapshot_queries import (
    compile_snapshot_filters,
    quote_identifier,
    snapshot_response_value,
)
from datepipette.infrastructure.object_storage import (
    LocalObjectStorage,
    ObjectStorageRegistry,
    ObjectStorageUnavailableError,
)


def _column(field: SnapshotQueryField) -> str:
    return f"source.{quote_identifier(field.storage_name)}"


def _dimension_key(field: SnapshotQueryField) -> str:
    return f"dimension_{field.field_id.hex}"


def _measure_key(measure_id: Any) -> str:
    return f"measure_{measure_id.hex}"


def _measure_expression(aggregation: ChartAggregation, field: SnapshotQueryField) -> str:
    column = _column(field)
    if aggregation is ChartAggregation.COUNT:
        return f"count({column})"
    if aggregation is ChartAggregation.COUNT_DISTINCT:
        return f"count(DISTINCT {column})"
    functions = {
        ChartAggregation.SUM: "sum",
        ChartAggregation.AVERAGE: "avg",
        ChartAggregation.MINIMUM: "min",
        ChartAggregation.MAXIMUM: "max",
    }
    return f"{functions[aggregation]}({column})"


def _measure_data_type(aggregation: ChartAggregation, field: SnapshotQueryField) -> str:
    if aggregation in {ChartAggregation.COUNT, ChartAggregation.COUNT_DISTINCT}:
        return "bigint"
    if aggregation is ChartAggregation.AVERAGE:
        return "numeric"
    return field.data_type


def _compile_query(
    context: ActiveSnapshotQueryContext,
    prepared: PreparedChartQuery,
) -> tuple[str, list[Any], tuple[ChartResultColumn, ...], tuple[SnapshotQueryField, ...]]:
    definition = prepared.definition
    fields = {field.field_id: field for field in context.fields}
    projections: list[str] = []
    result_columns: list[ChartResultColumn] = []
    response_fields: list[SnapshotQueryField] = []
    result_aliases: dict[tuple[ChartSortTargetType, Any], str] = {}

    for field in prepared.dimension_fields:
        key = _dimension_key(field)
        projections.append(f"{_column(field)} AS {quote_identifier(key)}")
        result_columns.append(
            ChartResultColumn(
                key=key,
                role=ChartResultColumnRole.DIMENSION,
                field_id=field.field_id,
                measure_id=None,
                label=field.label,
                data_type=field.data_type,
            )
        )
        response_fields.append(field)
        result_aliases[(ChartSortTargetType.DIMENSION, field.field_id)] = key

    for measure, field in zip(definition.measures, prepared.measure_fields, strict=True):
        key = _measure_key(measure.id)
        data_type = _measure_data_type(measure.aggregation, field)
        projections.append(
            f"{_measure_expression(measure.aggregation, field)} AS {quote_identifier(key)}"
        )
        result_columns.append(
            ChartResultColumn(
                key=key,
                role=ChartResultColumnRole.MEASURE,
                field_id=field.field_id,
                measure_id=measure.id,
                label=measure.label,
                data_type=data_type,
            )
        )
        response_fields.append(
            SnapshotQueryField(
                field_id=field.field_id,
                name=key,
                storage_name=key,
                data_type=data_type,
                nullable=True,
                visible=True,
                label=measure.label,
                display_format=field.display_format,
            )
        )
        result_aliases[(ChartSortTargetType.MEASURE, measure.id)] = key

    clauses, parameters = compile_snapshot_filters(definition.filters, fields)
    groups = [_column(field) for field in prepared.dimension_fields]
    order = [
        f"{quote_identifier(result_aliases[(item.target_type, item.target_id)])} "
        f"{item.direction.value.upper()} NULLS LAST"
        for item in definition.sorts
    ]
    sorted_dimensions = {
        item.target_id
        for item in definition.sorts
        if item.target_type is ChartSortTargetType.DIMENSION
    }
    order.extend(
        f"{quote_identifier(_dimension_key(field))} ASC NULLS LAST"
        for field in prepared.dimension_fields
        if field.field_id not in sorted_dimensions
    )
    statement = (
        "SELECT "
        + ", ".join(projections)
        + " FROM read_parquet(?) AS source"
        + (" WHERE " + " AND ".join(f"({clause})" for clause in clauses) if clauses else "")
        + (" GROUP BY " + ", ".join(groups) if groups else "")
        + (" ORDER BY " + ", ".join(order) if order else "")
        + " LIMIT ?"
    )
    return statement, parameters, tuple(result_columns), tuple(response_fields)


class DuckDbChartQueryExecutor:
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
        prepared: PreparedChartQuery,
        maximum_bytes: int,
    ) -> ChartQueryExecution:
        try:
            await asyncio.wait_for(self._semaphore.acquire(), self._timeout_seconds)
        except TimeoutError as exc:
            raise ChartQueryTimeoutError from exc
        try:
            return await self._query_once(
                context=context,
                prepared=prepared,
                maximum_bytes=maximum_bytes,
            )
        finally:
            self._semaphore.release()

    async def _query_once(
        self,
        *,
        context: ActiveSnapshotQueryContext,
        prepared: PreparedChartQuery,
        maximum_bytes: int,
    ) -> ChartQueryExecution:
        statement, parameters, columns, response_fields = _compile_query(context, prepared)
        try:
            materialized = self._storages.require(context.storage_backend).materialize(
                context.object_key,
                expected_byte_size=context.byte_size,
                expected_checksum_sha256=context.checksum_sha256,
            )
            path = await asyncio.to_thread(materialized.__enter__)
        except (OSError, ObjectStorageUnavailableError, ValueError) as exc:
            raise ChartQueryUnavailableError from exc
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
            raise ChartQueryUnavailableError from exc
        task = asyncio.create_task(
            asyncio.to_thread(
                self._execute,
                connection,
                statement,
                [str(path), *parameters, prepared.definition.limit],
                columns,
                response_fields,
                maximum_bytes,
            )
        )
        try:
            return await asyncio.wait_for(asyncio.shield(task), self._timeout_seconds)
        except TimeoutError as exc:
            connection.interrupt()
            with suppress(Exception):
                await asyncio.wait_for(asyncio.shield(task), 5)
            raise ChartQueryTimeoutError from exc
        except (duckdb.Error, OSError, TypeError, ValueError) as exc:
            raise ChartQueryUnavailableError from exc
        finally:
            if task.done():
                connection.close()
                await asyncio.to_thread(materialized.__exit__, None, None, None)
            else:

                def cleanup(_: asyncio.Task[ChartQueryExecution]) -> None:
                    connection.close()
                    with suppress(Exception):
                        materialized.__exit__(None, None, None)

                task.add_done_callback(cleanup)

    @staticmethod
    def _execute(
        connection: duckdb.DuckDBPyConnection,
        statement: str,
        parameters: list[Any],
        columns: tuple[ChartResultColumn, ...],
        response_fields: tuple[SnapshotQueryField, ...],
        maximum_bytes: int,
    ) -> ChartQueryExecution:
        records = connection.execute(statement, parameters).fetchall()
        rows: list[dict[str, Any]] = []
        encoded_bytes = 0
        truncated_by_bytes = False
        for record in records:
            row = {
                column.key: snapshot_response_value(response_fields[index], record[index])
                for index, column in enumerate(columns)
            }
            row_bytes = len(
                json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            )
            if encoded_bytes + row_bytes > maximum_bytes:
                if not rows:
                    raise ChartQueryResultTooLargeError
                truncated_by_bytes = True
                break
            rows.append(row)
            encoded_bytes += row_bytes
        return ChartQueryExecution(columns, tuple(rows), truncated_by_bytes)


class DuckDbLocalChartQueryExecutor(DuckDbChartQueryExecutor):
    """Compatibility adapter for focused local tests."""

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
