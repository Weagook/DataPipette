"""Bounded PostgreSQL execution for dataset draft previews."""

import base64
import json
import math
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import psycopg

from datepipette.application.dataset_preview import (
    SourceDatasetPreviewUnavailableError,
    SourcePreviewResult,
)
from datepipette.domain.connections import PostgresConnection
from datepipette.domain.datasets import DatasetDefinition, PreviewTruncationReason
from datepipette.infrastructure.dataset_sql import compile_bounded_select
from datepipette.infrastructure.source_postgres import open_read_only_source

_CURSOR_BATCH_ROWS = 25


def json_safe_preview_value(value: Any) -> Any:
    """Convert Psycopg values to an explicit precision-preserving JSON representation."""

    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return base64.b64encode(bytes(value)).decode("ascii")
    if isinstance(value, Mapping):
        return {str(key): json_safe_preview_value(item) for key, item in value.items()}
    if isinstance(value, Sequence):
        return [json_safe_preview_value(item) for item in value]
    return str(value)


class PsycopgSourceDatasetPreviewer:
    def __init__(self, connect_timeout_seconds: int, statement_timeout_seconds: int) -> None:
        self._connect_timeout = connect_timeout_seconds
        self._statement_timeout = statement_timeout_seconds

    async def preview(
        self,
        connection: PostgresConnection,
        *,
        password: str,
        ca_certificate: str | None,
        definition: DatasetDefinition,
        limit: int,
        maximum_bytes: int,
        statement_timeout_seconds: int | None = None,
    ) -> SourcePreviewResult:
        try:
            async with open_read_only_source(
                connection,
                password=password,
                ca_certificate=ca_certificate,
                connect_timeout_seconds=self._connect_timeout,
                statement_timeout_seconds=(statement_timeout_seconds or self._statement_timeout),
            ) as source:
                query = compile_bounded_select(definition)
                cursor_name = f"datepipette_preview_{uuid4().hex}"
                async with source.cursor(name=cursor_name) as cursor:
                    await cursor.execute(query, (limit + 1,))
                    rows: list[dict[str, Any]] = []
                    encoded_bytes = 0
                    names = tuple(field.name for field in definition.fields)
                    while batch := await cursor.fetchmany(_CURSOR_BATCH_ROWS):
                        for raw_row in batch:
                            if len(rows) >= limit:
                                return SourcePreviewResult(
                                    rows=tuple(rows),
                                    truncation_reason=PreviewTruncationReason.ROW_LIMIT,
                                )
                            if len(raw_row) != len(names):
                                raise ValueError("Source preview column count changed")
                            row = {
                                name: json_safe_preview_value(value)
                                for name, value in zip(names, raw_row, strict=True)
                            }
                            row_bytes = len(
                                json.dumps(
                                    row,
                                    ensure_ascii=False,
                                    separators=(",", ":"),
                                ).encode("utf-8")
                            )
                            if encoded_bytes + row_bytes > maximum_bytes:
                                return SourcePreviewResult(
                                    rows=tuple(rows),
                                    truncation_reason=PreviewTruncationReason.BYTE_LIMIT,
                                )
                            rows.append(row)
                            encoded_bytes += row_bytes
                    return SourcePreviewResult(rows=tuple(rows), truncation_reason=None)
        except (TimeoutError, psycopg.Error, TypeError, ValueError) as exc:
            raise SourceDatasetPreviewUnavailableError from exc
