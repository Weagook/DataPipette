"""The columnar CSV refresh stream must publish exactly what the row stream publishes."""

import asyncio
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest
from sqlalchemy.engine import make_url

from datapipette.config import Settings
from datapipette.domain.connections import PostgresConnection, PostgresSslMode
from datapipette.domain.datasets import (
    DatasetValidation,
    DatasetValidationState,
    FieldDisplay,
    SqlDatasetDefinition,
    SqlField,
    ValidatedDatasetField,
)
from datapipette.infrastructure import parquet_snapshots
from datapipette.infrastructure.object_storage import LocalObjectStorage
from datapipette.infrastructure.parquet_snapshots import (
    ParquetSnapshotExecutor,
    RefreshRowLimitExceededError,
    _CsvStreamRejectedError,
    _ExecutionContext,
)

SCHEMA = "csv_stream_parity"
SETTINGS = Settings(environment="test")
URL = make_url(SETTINGS.metadata_database_url.get_secret_value())
COLUMNS = (
    ("id", "bigint NOT NULL"),
    ("flag", "boolean"),
    ("small", "smallint"),
    ("medium", "integer"),
    ("large", "bigint"),
    ("single", "real"),
    ("double", "double precision"),
    ("money_value", "numeric(12,2)"),
    ("wide_decimal", "numeric(38,6)"),
    ("label", "text"),
    ("code", "varchar(20)"),
    ("padded", "char(5)"),
    ("day", "date"),
    ("local_time", "timestamp"),
    ("instant", "timestamptz"),
    ("identifier", "uuid"),
)
ROWS = """
(1, true, -32768, -2147483648, -9223372036854775808, 'NaN', 'Infinity', -12.30,
 12345678901234567890123456789012.123456, 'plain', 'café', 'ab',
 '1999-12-31', '2026-01-02 03:04:05.123456', '2026-01-02 08:04:05.5+05',
 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'),
(2, false, 32767, 2147483647, 9223372036854775807, '-Infinity', 1.7976931348623157e308, 0,
 -0.000001, '', '', '', '2026-02-28', '2026-02-28 23:59:59', '2026-02-28 23:59:59Z',
 '00000000-0000-0000-0000-000000000000'),
(3, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL),
(4, true, 0, 0, 0, 3.4028235e38, 0.1, 99999999.99, 1, E'line one\\nline "two", three',
 'tab\there', 'x', '0001-01-01', '0001-01-01 00:00:00', '1970-01-01 00:00:00+00',
 'ffffffff-ffff-ffff-ffff-ffffffffffff')
"""


async def _execute(statement: str) -> None:
    async with await psycopg.AsyncConnection.connect(
        host=URL.host,
        port=URL.port,
        dbname=URL.database,
        user=URL.username,
        password=URL.password,
        autocommit=True,
    ) as connection:
        await connection.execute(statement)


async def _types() -> list[tuple[str, str, bool]]:
    async with await psycopg.AsyncConnection.connect(
        host=URL.host,
        port=URL.port,
        dbname=URL.database,
        user=URL.username,
        password=URL.password,
    ) as connection:
        cursor = await connection.execute(
            "SELECT attname, format_type(atttypid, atttypmod), NOT attnotnull "
            "FROM pg_attribute WHERE attrelid = %s::regclass AND attnum > 0 "
            "AND NOT attisdropped ORDER BY attnum",
            (f"{SCHEMA}.samples",),
        )
        return [
            (str(name), str(kind), bool(nullable))
            for name, kind, nullable in await cursor.fetchall()
        ]


@pytest.fixture(autouse=True)
def source_table() -> Generator[None, None, None]:
    columns = ", ".join(f"{name} {kind}" for name, kind in COLUMNS)
    asyncio.run(_execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
    asyncio.run(_execute(f"CREATE SCHEMA {SCHEMA}"))
    asyncio.run(_execute(f"CREATE TABLE {SCHEMA}.samples ({columns})"))
    asyncio.run(_execute(f"INSERT INTO {SCHEMA}.samples VALUES {ROWS}"))
    yield
    asyncio.run(_execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))


def _context(*, where: str = "", not_null: set[str] | None = None) -> _ExecutionContext:
    types = asyncio.run(_types())
    ids = {name: uuid4() for name, _type, _nullable in types}
    names = ", ".join(name for name, _type, _nullable in types)
    definition = SqlDatasetDefinition(
        statement=f"SELECT {names} FROM {SCHEMA}.samples {where} ORDER BY id",
        fields=tuple(SqlField(ids[name], name, FieldDisplay(name)) for name, _t, _n in types),
    )
    validation = DatasetValidation(
        state=DatasetValidationState.VALID,
        validated_revision=1,
        validated_at=datetime.now(UTC),
        compiled_sql=None,
        result_schema=tuple(
            ValidatedDatasetField(
                ids[name], name, kind, nullable and name not in (not_null or set())
            )
            for name, kind, nullable in types
        ),
        plan=None,
        findings=(),
    )
    now = datetime.now(UTC)
    connection = PostgresConnection(
        id=uuid4(),
        name="Parity source",
        host=str(URL.host),
        port=int(URL.port or 5432),
        database_name=str(URL.database),
        username=str(URL.username),
        ssl_mode=PostgresSslMode.DISABLE,
        has_ca_certificate=False,
        created_by=uuid4(),
        last_tested_at=None,
        last_test_succeeded=None,
        last_test_error_code=None,
        created_at=now,
        updated_at=now,
    )
    return _ExecutionContext(connection, str(URL.password), None, definition, validation)


def _executor(root: Path, *, maximum_rows: int = 1000) -> ParquetSnapshotExecutor:
    unused: Any = None
    return ParquetSnapshotExecutor(
        unused,
        unused,
        LocalObjectStorage(root),
        root,
        connect_timeout_seconds=5,
        statement_timeout_seconds=30,
        batch_rows=2,
        authorizer=unused,
        maximum_rows=maximum_rows,
    )


def _publish(root: Path, context: _ExecutionContext, *, maximum_rows: int = 1000) -> Any:
    run: Any = SimpleNamespace(id=uuid4(), dataset_id=UUID(int=1))
    artifact = asyncio.run(
        _executor(root, maximum_rows=maximum_rows)._write(run, context, asyncio.Event())
    )
    return artifact, pq.read_table(root / artifact.object_key)


def _force_row_stream(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def reject(*_args: Any, **_kwargs: Any) -> int:
        calls.append("csv")
        raise _CsvStreamRejectedError

    monkeypatch.setattr(parquet_snapshots, "_write_csv_stream", reject)
    return calls


def test_csv_stream_matches_row_stream_despite_hostile_session_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Prove the stream pins its own text formats instead of inheriting the role's.
    monkeypatch.setenv(
        "PGOPTIONS",
        "-c TimeZone=Asia/Tokyo -c DateStyle=SQL,DMY -c extra_float_digits=0",
    )
    context = _context()
    original = parquet_snapshots._write_csv_stream
    csv_rows: list[int] = []

    def spy(*args: Any, **kwargs: Any) -> int:
        csv_rows.append(original(*args, **kwargs))
        return csv_rows[-1]

    monkeypatch.setattr(parquet_snapshots, "_write_csv_stream", spy)
    csv_artifact, csv_table = _publish(tmp_path / "csv", context)
    assert csv_rows == [4]
    calls = _force_row_stream(monkeypatch)
    row_artifact, row_table = _publish(tmp_path / "rows", context)

    assert calls == ["csv"]
    assert csv_artifact.row_count == row_artifact.row_count == 4
    assert csv_table.schema.equals(row_table.schema, check_metadata=True)
    # NaN never equals itself, so compare it by name.
    assert _without_nan(csv_table.to_pylist()) == _without_nan(row_table.to_pylist())
    names = dict(zip((name for name, _kind in COLUMNS), csv_table.schema.names, strict=True))
    first = csv_table.to_pylist()[0]
    assert first[names["instant"]] == datetime(2026, 1, 2, 3, 4, 5, 500000, tzinfo=UTC)
    assert first[names["padded"]] == "ab   "
    assert [row[names["label"]] for row in csv_table.to_pylist()[1:3]] == ["", None]


def _without_nan(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            key: ("nan" if isinstance(value, float) and value != value else value)
            for key, value in row.items()
        }
        for row in rows
    ]


def test_csv_stream_publishes_an_empty_typed_snapshot(tmp_path: Path) -> None:
    artifact, table = _publish(tmp_path, _context(where="WHERE id < 0"))

    assert artifact.row_count == 0
    assert table.num_rows == 0
    assert len(table.schema) == len(COLUMNS)


def test_csv_stream_enforces_row_limit_and_non_null_contract(tmp_path: Path) -> None:
    with pytest.raises(RefreshRowLimitExceededError):
        _publish(tmp_path / "limit", _context(), maximum_rows=3)
    with pytest.raises(ValueError, match="Non-nullable source field returned null"):
        _publish(tmp_path / "null", _context(not_null={"label"}))
    assert not list((tmp_path / "limit").rglob("*.parquet"))
