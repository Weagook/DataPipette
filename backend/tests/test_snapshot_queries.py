"""Typed snapshot query service, cursor, and local DuckDB adapter tests."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

from datapipette.application.access_control import DatasetAccessAuthorizer
from datapipette.application.query_safety import (
    InteractiveQueryRuntime,
    ManagedQuerySafetyPolicy,
)
from datapipette.application.snapshot_queries import (
    DatasetSnapshotNotFoundError,
    DatasetSnapshotQueryCapacityExceededError,
    DatasetSnapshotQueryService,
    DatasetSnapshotQueryTimeoutError,
    DatasetSnapshotResultTooLargeError,
    DatasetSnapshotUnavailableError,
    InvalidSnapshotCursorError,
    InvalidSnapshotQueryError,
    supported_filter_operators,
)
from datapipette.domain.access_control import DatasetAccess, DatasetPermission
from datapipette.domain.datasets import FieldDisplayFormat, QuerySafetyPolicy
from datapipette.domain.identity import AccountStatus, SystemRole, User
from datapipette.domain.snapshot_queries import (
    ActiveSnapshotQueryContext,
    DatasetDataState,
    DatasetFreshness,
    SnapshotFilterOperator,
    SnapshotQuery,
    SnapshotQueryCursorPosition,
    SnapshotQueryExecution,
    SnapshotQueryField,
    SnapshotQueryFilter,
    SnapshotQuerySort,
    SnapshotRefreshState,
    SnapshotSortDirection,
)
from datapipette.infrastructure.duckdb_runtime import (
    DuckDbQueryConcurrencyLimiter,
    InteractiveQueryAdmissionUnavailableError,
)
from datapipette.infrastructure.duckdb_snapshot_queries import (
    DuckDbLocalSnapshotQueryExecutor,
)
from datapipette.infrastructure.snapshot_cursor import EncryptedSnapshotCursorCodec

DATASET_ID = uuid4()
SNAPSHOT_ID = uuid4()
BOOL_ID = uuid4()
INT_ID = uuid4()
NUMBER_ID = uuid4()
TEXT_ID = uuid4()
DATE_ID = uuid4()
DATETIME_ID = uuid4()
TIME_ID = uuid4()
JSON_ID = uuid4()
BINARY_ID = uuid4()

RUNTIME = InteractiveQueryRuntime(
    timeout_seconds=5,
    memory_limit_mb=128,
    threads=2,
    concurrency=2,
    temporary_directory_limit_mb=128,
    maximum_response_bytes=65_536,
)

FIELDS = (
    SnapshotQueryField(BOOL_ID, "active", "p_bool", "boolean", False, True, "Active", None),
    SnapshotQueryField(INT_ID, "record_id", "p_int", "bigint", False, True, "ID", None),
    SnapshotQueryField(
        NUMBER_ID, "amount", "p_number", "numeric(12,2)", True, True, "Amount", None
    ),
    SnapshotQueryField(TEXT_ID, "label", "p_text", "text", True, True, "Label", None),
    SnapshotQueryField(
        DATE_ID,
        "day",
        "p_date",
        "date",
        True,
        True,
        "Day",
        FieldDisplayFormat.DATE_DD_MM_YYYY,
    ),
    SnapshotQueryField(
        DATETIME_ID,
        "occurred_at",
        "p_datetime",
        "timestamp with time zone",
        True,
        True,
        "Occurred at",
        None,
    ),
    SnapshotQueryField(
        TIME_ID, "clock", "p_time", "time without time zone", True, True, "Clock", None
    ),
    SnapshotQueryField(JSON_ID, "payload", "p_json", "jsonb", True, True, "Payload", None),
    SnapshotQueryField(BINARY_ID, "raw", "p_binary", "bytea", True, False, "Raw", None),
)


def _context(object_key: str = "snapshot.parquet") -> ActiveSnapshotQueryContext:
    return ActiveSnapshotQueryContext(
        dataset_id=DATASET_ID,
        dataset_name="Orders",
        snapshot_id=SNAPSHOT_ID,
        storage_backend="local",
        object_key=object_key,
        activated_at=datetime(2026, 1, 1, tzinfo=UTC),
        snapshot_row_count=3,
        fields=FIELDS,
        refresh_state=SnapshotRefreshState.IDLE,
        last_refresh_error=None,
        is_stale=False,
    )


def _admin(role: SystemRole = SystemRole.ADMIN) -> User:
    now = datetime.now(UTC)
    return User(uuid4(), "admin@example.com", "Admin", role, AccountStatus.ACTIVE, None, now, now)


class FakeStore:
    def __init__(
        self,
        context: ActiveSnapshotQueryContext | None = None,
        permissions: frozenset[DatasetPermission] = frozenset(DatasetPermission),
    ) -> None:
        self.context = context
        self.permissions = permissions
        self.freshness = (
            DatasetFreshness(
                dataset_id=context.dataset_id,
                data_state=DatasetDataState.READY,
                snapshot_id=context.snapshot_id,
                snapshot_row_count=context.snapshot_row_count,
                last_success_at=context.activated_at,
                refresh_state=context.refresh_state,
                active_refresh_run_id=None,
                refresh_requested_at=None,
                refresh_started_at=None,
                last_refresh_error=context.last_refresh_error,
                last_refresh_error_at=context.last_refresh_error_at,
                is_stale=context.is_stale,
            )
            if context is not None
            else None
        )
        self.audit: list[dict[str, Any]] = []

    async def get_freshness(self, dataset_id: UUID) -> DatasetFreshness | None:
        return self.freshness if dataset_id == DATASET_ID else None

    async def get_active_context(self, dataset_id: UUID) -> ActiveSnapshotQueryContext | None:
        return self.context if dataset_id == DATASET_ID else None

    async def get_dataset_access(self, *, actor_id: UUID, dataset_id: UUID) -> DatasetAccess | None:
        if dataset_id != DATASET_ID:
            return None
        return DatasetAccess(dataset_id, False, self.permissions)

    async def record_query(self, **values: Any) -> None:
        self.audit.append(values)


class FakeExecutor:
    def __init__(
        self,
        result: SnapshotQueryExecution | Exception | None = None,
    ) -> None:
        self.result = result or SnapshotQueryExecution(({"record_id": 1},), None, False)
        self.calls: list[dict[str, Any]] = []

    async def query(self, **values: Any) -> SnapshotQueryExecution:
        self.calls.append(values)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakePolicyStore:
    def __init__(self, maximum_bytes: int = 65_536, revision: int = 7) -> None:
        self.managed = ManagedQuerySafetyPolicy(
            QuerySafetyPolicy(interactive_query_max_response_bytes=maximum_bytes),
            revision,
            None,
            None,
        )

    async def get(self) -> ManagedQuerySafetyPolicy:
        return self.managed


def _service(
    store: FakeStore | None = None,
    executor: FakeExecutor | None = None,
    maximum_bytes: int = 65_536,
) -> tuple[DatasetSnapshotQueryService, FakeStore, FakeExecutor]:
    resolved_store = store or FakeStore(_context())
    resolved_executor = executor or FakeExecutor()
    return (
        DatasetSnapshotQueryService(
            resolved_store,
            resolved_executor,
            EncryptedSnapshotCursorCodec("test-key"),
            DatasetAccessAuthorizer(resolved_store),
            FakePolicyStore(maximum_bytes),
        ),
        resolved_store,
        resolved_executor,
    )


def _query(**changes: Any) -> SnapshotQuery:
    values: dict[str, Any] = {
        "field_ids": (INT_ID,),
        "filters": (),
        "sorts": (),
        "page_size": 10,
        "cursor": None,
    }
    values.update(changes)
    return SnapshotQuery(**values)


def test_exploration_returns_published_display_and_filter_capabilities() -> None:
    service, _, _ = _service()

    result = asyncio.run(service.exploration(actor=_admin(), dataset_id=DATASET_ID))

    assert result.dataset_name == "Orders"
    assert result.can_export is True
    assert result.installation_timezone == "UTC"
    assert result.snapshot_id == SNAPSHOT_ID
    assert result.default_field_ids == tuple(field.field_id for field in FIELDS if field.visible)
    date_column = next(column for column in result.columns if column.field_id == DATE_ID)
    assert date_column.label == "Day"
    assert date_column.display_format is FieldDisplayFormat.DATE_DD_MM_YYYY
    assert date_column.filter_operators == supported_filter_operators(FIELDS[4])
    assert SnapshotFilterOperator.BETWEEN in date_column.filter_operators
    binary_column = next(column for column in result.columns if column.field_id == BINARY_ID)
    assert binary_column.visible is False
    assert binary_column.filter_operators == (
        SnapshotFilterOperator.IS_NULL,
        SnapshotFilterOperator.IS_NOT_NULL,
    )
    unsupported = SnapshotQueryField(
        uuid4(), "tags", "p_tags", "integer[]", True, True, "Tags", None
    )
    assert supported_filter_operators(unsupported) == ()
    with pytest.raises(DatasetSnapshotNotFoundError):
        asyncio.run(
            service.exploration(
                actor=_admin(SystemRole.VIEWER),
                dataset_id=DATASET_ID,
            )
        )


def test_exploration_default_projection_respects_query_field_limit() -> None:
    fields = tuple(
        SnapshotQueryField(
            uuid4(),
            f"field_{index}",
            f"p_{index}",
            "text",
            True,
            True,
            f"Field {index}",
            None,
        )
        for index in range(101)
    )
    service, _, _ = _service(FakeStore(replace(_context(), fields=fields)))

    result = asyncio.run(service.exploration(actor=_admin(), dataset_id=DATASET_ID))

    assert len(result.columns) == 101
    assert result.default_field_ids == tuple(field.field_id for field in fields[:100])


def test_exploration_reports_effective_export_capability() -> None:
    store = FakeStore(_context(), frozenset({DatasetPermission.VIEW_ROWS}))
    service, _, _ = _service(store)

    result = asyncio.run(
        service.exploration(actor=_admin(SystemRole.ANALYST), dataset_id=DATASET_ID)
    )

    assert result.can_export is False


def test_service_normalizes_typed_filters_and_round_trips_cursor() -> None:
    executor = FakeExecutor(
        SnapshotQueryExecution(
            ({"record_id": 1},),
            SnapshotQueryCursorPosition(("Zulu",), 7),
            False,
        )
    )
    service, store, _ = _service(executor=executor)
    filters = (
        SnapshotQueryFilter(BOOL_ID, SnapshotFilterOperator.EQUALS, True),
        SnapshotQueryFilter(INT_ID, SnapshotFilterOperator.GREATER_THAN, 0),
        SnapshotQueryFilter(
            NUMBER_ID,
            SnapshotFilterOperator.BETWEEN,
            values=("1.20", "9.90"),
        ),
        SnapshotQueryFilter(TEXT_ID, SnapshotFilterOperator.STARTS_WITH, "A"),
        SnapshotQueryFilter(DATE_ID, SnapshotFilterOperator.LESS_THAN, "2026-02-01"),
        SnapshotQueryFilter(
            DATETIME_ID,
            SnapshotFilterOperator.GREATER_THAN_OR_EQUAL,
            "2026-01-01T00:00:00Z",
        ),
        SnapshotQueryFilter(TIME_ID, SnapshotFilterOperator.EQUALS, "12:30:00"),
        SnapshotQueryFilter(JSON_ID, SnapshotFilterOperator.EQUALS, {"b": 2, "a": 1}),
        SnapshotQueryFilter(BINARY_ID, SnapshotFilterOperator.IS_NULL),
    )
    sorts = (SnapshotQuerySort(TEXT_ID, SnapshotSortDirection.ASCENDING),)
    first = asyncio.run(
        service.query(
            actor=_admin(),
            dataset_id=DATASET_ID,
            value=_query(filters=filters, sorts=sorts),
            request_id="request-1",
        )
    )
    assert first.next_cursor is not None
    assert first.policy_revision == 7
    assert executor.calls[0]["runtime"].maximum_response_bytes == 65_536
    normalized = executor.calls[0]["filters"]
    assert normalized[2].values == (Decimal("1.20"), Decimal("9.90"))
    assert normalized[4].value == date(2026, 2, 1)
    assert normalized[5].value == datetime(2026, 1, 1, tzinfo=UTC)
    assert normalized[6].value == time(12, 30)
    assert normalized[7].value == '{"a":1,"b":2}'
    assert store.audit[0]["error_code"] is None
    assert store.audit[0]["policy_revision"] == 7

    asyncio.run(
        service.query(
            actor=_admin(),
            dataset_id=DATASET_ID,
            value=_query(filters=filters, sorts=sorts, cursor=first.next_cursor),
            request_id="request-2",
        )
    )
    assert executor.calls[1]["position"] == SnapshotQueryCursorPosition(("Zulu",), 7)


@pytest.mark.parametrize(
    "query",
    [
        _query(field_ids=(INT_ID, INT_ID)),
        _query(field_ids=(uuid4(),)),
        _query(sorts=(SnapshotQuerySort(TEXT_ID, SnapshotSortDirection.ASCENDING),) * 2),
        _query(filters=(SnapshotQueryFilter(BOOL_ID, SnapshotFilterOperator.CONTAINS, "x"),)),
        _query(filters=(SnapshotQueryFilter(BINARY_ID, SnapshotFilterOperator.EQUALS, "00"),)),
        _query(filters=(SnapshotQueryFilter(NUMBER_ID, SnapshotFilterOperator.EQUALS, "nan"),)),
        _query(
            filters=(
                SnapshotQueryFilter(
                    NUMBER_ID,
                    SnapshotFilterOperator.BETWEEN,
                    values=("1",),
                ),
            )
        ),
        _query(filters=(SnapshotQueryFilter(TEXT_ID, SnapshotFilterOperator.IN, values=()),)),
        _query(filters=(SnapshotQueryFilter(TEXT_ID, SnapshotFilterOperator.IS_NULL, "x"),)),
    ],
)
def test_service_rejects_invalid_query_semantics(query: SnapshotQuery) -> None:
    service, _, _ = _service()
    with pytest.raises(InvalidSnapshotQueryError):
        asyncio.run(
            service.query(
                actor=_admin(),
                dataset_id=DATASET_ID,
                value=query,
                request_id="request",
            )
        )


def test_service_rejects_unknown_dataset_role_and_cursor_mismatch() -> None:
    service, _, _ = _service(FakeStore(None))
    with pytest.raises(DatasetSnapshotNotFoundError):
        asyncio.run(
            service.query(
                actor=_admin(),
                dataset_id=DATASET_ID,
                value=_query(),
                request_id="request",
            )
        )
    service, _, _ = _service()
    with pytest.raises(DatasetSnapshotNotFoundError):
        asyncio.run(
            service.query(
                actor=_admin(SystemRole.VIEWER),
                dataset_id=DATASET_ID,
                value=_query(),
                request_id="request",
            )
        )
    with pytest.raises(InvalidSnapshotCursorError):
        asyncio.run(
            service.query(
                actor=_admin(),
                dataset_id=DATASET_ID,
                value=_query(cursor="not-a-cursor"),
                request_id="request",
            )
        )


def test_service_returns_freshness_and_conceals_denied_access() -> None:
    service, store, _ = _service()
    result = asyncio.run(service.freshness(actor=_admin(), dataset_id=DATASET_ID))
    assert result is store.freshness
    with pytest.raises(DatasetSnapshotNotFoundError):
        asyncio.run(
            service.freshness(
                actor=_admin(SystemRole.VIEWER),
                dataset_id=DATASET_ID,
            )
        )


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (DatasetSnapshotUnavailableError(), "snapshot_query_unavailable"),
        (DatasetSnapshotQueryTimeoutError(), "snapshot_query_timeout"),
        (DatasetSnapshotResultTooLargeError(), "snapshot_result_too_large"),
    ],
)
def test_service_audits_safe_executor_failures(error: Exception, code: str) -> None:
    service, store, _ = _service(executor=FakeExecutor(error))
    with pytest.raises(type(error)):
        asyncio.run(
            service.query(
                actor=_admin(),
                dataset_id=DATASET_ID,
                value=_query(),
                request_id="request",
            )
        )
    assert store.audit[0]["error_code"] == code
    assert "filter" not in store.audit[0]


def test_service_audits_capacity_rejection() -> None:
    error = DatasetSnapshotQueryCapacityExceededError()
    service, store, _ = _service(executor=FakeExecutor(error))
    with pytest.raises(DatasetSnapshotQueryCapacityExceededError):
        asyncio.run(
            service.query(
                actor=_admin(),
                dataset_id=DATASET_ID,
                value=_query(),
                request_id="request",
            )
        )
    assert store.audit[0]["error_code"] == "interactive_query_capacity_exceeded"


def test_cursor_is_encrypted_typed_and_tamper_evident() -> None:
    codec = EncryptedSnapshotCursorCodec("installation-secret")
    token = codec.encode(
        dataset_id=DATASET_ID,
        snapshot_id=SNAPSHOT_ID,
        query_digest="a" * 64,
        position=SnapshotQueryCursorPosition(
            (
                None,
                True,
                7,
                1.5,
                "private value",
                Decimal("2.30"),
                datetime(2026, 1, 2, tzinfo=UTC),
                date(2026, 1, 2),
                time(3, 4, 5),
                b"\x00\xff",
            ),
            12,
        ),
    )
    assert "private" not in token
    decoded = codec.decode(token)
    assert decoded.dataset_id == DATASET_ID
    assert decoded.position.sort_values[5] == Decimal("2.30")
    assert decoded.position.file_row_number == 12
    with pytest.raises(ValueError):
        codec.decode(token[:-1] + ("A" if token[-1] != "A" else "B"))


def _write_fixture(path: Path) -> None:
    table = pa.table(
        {
            "p_bool": [True, False, True],
            "p_int": [1, 2, 3],
            "p_number": pa.array(
                [Decimal("1.20"), None, Decimal("9.90")],
                type=pa.decimal128(12, 2),
            ),
            "p_text": ["same", None, "same"],
            "p_date": [date(2026, 1, 1), None, date(2026, 1, 3)],
            "p_datetime": pa.array(
                [
                    datetime(2026, 1, 1, tzinfo=UTC),
                    None,
                    datetime(2026, 1, 3, tzinfo=UTC),
                ],
                type=pa.timestamp("us", tz="UTC"),
            ),
            "p_time": [time(1, 2), None, time(3, 4)],
            "p_json": ['{"a":1}', None, "{}"],
            "p_binary": [b"a", None, b"c"],
        }
    )
    pq.write_table(table, path)


def _executor(root: Path, timeout: float = 5) -> DuckDbLocalSnapshotQueryExecutor:
    del timeout
    return DuckDbLocalSnapshotQueryExecutor(root)


class StaticAdmission:
    def __init__(self, *, admitted: bool = True, unavailable: bool = False) -> None:
        self.admitted = admitted
        self.unavailable = unavailable

    @asynccontextmanager
    async def slot(self, _maximum_concurrent_queries: int) -> AsyncIterator[bool]:
        if self.unavailable:
            raise InteractiveQueryAdmissionUnavailableError
        yield self.admitted


@pytest.mark.parametrize(
    ("admission", "error"),
    [
        (StaticAdmission(admitted=False), DatasetSnapshotQueryCapacityExceededError),
        (StaticAdmission(unavailable=True), DatasetSnapshotUnavailableError),
    ],
)
def test_duckdb_executor_maps_distributed_admission_failures(
    tmp_path: Path,
    admission: StaticAdmission,
    error: type[Exception],
) -> None:
    executor = DuckDbLocalSnapshotQueryExecutor(
        tmp_path,
        limiter=DuckDbQueryConcurrencyLimiter(admission),
    )
    with pytest.raises(error):
        asyncio.run(
            executor.query(
                context=_context(),
                selected_fields=(FIELDS[1],),
                filters=(),
                sorts=(),
                position=None,
                page_size=1,
                runtime=RUNTIME,
            )
        )


def test_duckdb_executor_normalizes_rows_and_pages_null_sort_keys(tmp_path: Path) -> None:
    _write_fixture(tmp_path / "snapshot.parquet")
    executor = _executor(tmp_path)
    sorts = (SnapshotQuerySort(TEXT_ID, SnapshotSortDirection.ASCENDING),)
    first = asyncio.run(
        executor.query(
            context=_context(),
            selected_fields=FIELDS,
            filters=(SnapshotQueryFilter(INT_ID, SnapshotFilterOperator.GREATER_THAN, 0),),
            sorts=sorts,
            position=None,
            page_size=2,
            runtime=replace(RUNTIME, maximum_response_bytes=20_000),
        )
    )
    assert [row["record_id"] for row in first.rows] == [1, 3]
    assert first.rows[0]["amount"] == "1.20"
    assert first.rows[0]["occurred_at"] == "2026-01-01T00:00:00+00:00"
    assert first.rows[0]["payload"] == {"a": 1}
    assert first.rows[0]["raw"] == "YQ=="
    assert first.next_position is not None
    second = asyncio.run(
        executor.query(
            context=_context(),
            selected_fields=(FIELDS[1],),
            filters=(),
            sorts=sorts,
            position=first.next_position,
            page_size=2,
            runtime=replace(RUNTIME, maximum_response_bytes=20_000),
        )
    )
    assert second.rows == ({"record_id": 2},)
    assert second.next_position is None

    multi_sort = (
        SnapshotQuerySort(BOOL_ID, SnapshotSortDirection.ASCENDING),
        SnapshotQuerySort(TEXT_ID, SnapshotSortDirection.ASCENDING),
        SnapshotQuerySort(INT_ID, SnapshotSortDirection.DESCENDING),
    )
    multi_first = asyncio.run(
        executor.query(
            context=_context(),
            selected_fields=(FIELDS[1],),
            filters=(),
            sorts=multi_sort,
            position=None,
            page_size=1,
            runtime=replace(RUNTIME, maximum_response_bytes=20_000),
        )
    )
    assert multi_first.next_position is not None
    multi_second = asyncio.run(
        executor.query(
            context=_context(),
            selected_fields=(FIELDS[1],),
            filters=(),
            sorts=multi_sort,
            position=multi_first.next_position,
            page_size=2,
            runtime=replace(RUNTIME, maximum_response_bytes=20_000),
        )
    )
    assert [row["record_id"] for row in (*multi_first.rows, *multi_second.rows)] == [
        2,
        3,
        1,
    ]


def test_duckdb_executor_enforces_paths_bytes_and_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_fixture(tmp_path / "snapshot.parquet")
    executor = _executor(tmp_path)
    truncated = asyncio.run(
        executor.query(
            context=_context(),
            selected_fields=(FIELDS[1], FIELDS[3]),
            filters=(),
            sorts=(),
            position=None,
            page_size=3,
            runtime=replace(RUNTIME, maximum_response_bytes=45),
        )
    )
    assert truncated.truncated_by_bytes
    assert truncated.next_position is not None
    with pytest.raises(DatasetSnapshotResultTooLargeError):
        asyncio.run(
            executor.query(
                context=_context(),
                selected_fields=(FIELDS[1], FIELDS[3]),
                filters=(),
                sorts=(),
                position=None,
                page_size=3,
                runtime=replace(RUNTIME, maximum_response_bytes=1),
            )
        )
    for key in ("missing.parquet", "../outside.parquet"):
        with pytest.raises(DatasetSnapshotUnavailableError):
            asyncio.run(
                executor.query(
                    context=_context(key),
                    selected_fields=(FIELDS[1],),
                    filters=(),
                    sorts=(),
                    position=None,
                    page_size=1,
                    runtime=replace(RUNTIME, maximum_response_bytes=100),
                )
            )

    def slow_execute(*args: Any, **kwargs: Any) -> SnapshotQueryExecution:
        import time as time_module

        time_module.sleep(0.05)
        return SnapshotQueryExecution((), None, False)

    monkeypatch.setattr(executor, "_execute", slow_execute)
    with pytest.raises(DatasetSnapshotQueryTimeoutError):
        asyncio.run(
            executor.query(
                context=_context(),
                selected_fields=(FIELDS[1],),
                filters=(),
                sorts=(),
                position=None,
                page_size=1,
                runtime=replace(
                    RUNTIME,
                    timeout_seconds=0.001,
                    maximum_response_bytes=100,
                ),
            )
        )
