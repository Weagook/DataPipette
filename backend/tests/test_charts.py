"""Chart specification, application service, and DuckDB aggregate-query tests."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

from datepipette.application.access_control import DatasetAccessAuthorizer
from datepipette.application.charts import (
    ChartCreate,
    ChartDatasetNotAvailableError,
    ChartNotFoundError,
    ChartRevisionConflictError,
    ChartService,
    InvalidChartCursorError,
    InvalidChartDefinitionError,
    prepare_chart_query,
)
from datepipette.domain.access_control import DatasetAccess, DatasetPermission
from datepipette.domain.charts import (
    Chart,
    ChartAggregation,
    ChartDefinition,
    ChartDimension,
    ChartMeasure,
    ChartSort,
    ChartSortTargetType,
    ChartTimeBucket,
    ChartType,
)
from datepipette.domain.identity import AccountStatus, AccountType, SystemRole, User
from datepipette.domain.snapshot_queries import (
    ActiveSnapshotQueryContext,
    SnapshotFilterOperator,
    SnapshotQueryField,
    SnapshotQueryFilter,
    SnapshotRefreshState,
    SnapshotSortDirection,
)
from datepipette.infrastructure.duckdb_chart_queries import DuckDbLocalChartQueryExecutor

NOW = datetime(2026, 10, 12, tzinfo=UTC)
DATASET_ID = uuid4()
SNAPSHOT_ID = uuid4()
REGION_ID = uuid4()
AMOUNT_ID = uuid4()
ORDER_ID = uuid4()
OCCURRED_AT_ID = uuid4()
MEASURE_ID = uuid4()

FIELDS = (
    SnapshotQueryField(REGION_ID, "region", "region", "text", False, True, "Region", None),
    SnapshotQueryField(AMOUNT_ID, "amount", "amount", "numeric(12,2)", True, True, "Amount", None),
    SnapshotQueryField(ORDER_ID, "order_id", "order_id", "bigint", False, True, "Order", None),
    SnapshotQueryField(
        OCCURRED_AT_ID,
        "occurred_at",
        "occurred_at",
        "timestamp with time zone",
        False,
        True,
        "Occurred at",
        None,
    ),
)


def _context(object_key: str = "snapshot.parquet") -> ActiveSnapshotQueryContext:
    return ActiveSnapshotQueryContext(
        dataset_id=DATASET_ID,
        snapshot_id=SNAPSHOT_ID,
        storage_backend="local",
        object_key=object_key,
        activated_at=NOW,
        snapshot_row_count=4,
        fields=FIELDS,
        refresh_state=SnapshotRefreshState.IDLE,
        last_refresh_error=None,
        is_stale=False,
    )


def _definition() -> ChartDefinition:
    return ChartDefinition(
        chart_type=ChartType.BAR,
        dimensions=(ChartDimension(REGION_ID),),
        measures=(ChartMeasure(MEASURE_ID, AMOUNT_ID, ChartAggregation.SUM, "Revenue"),),
        filters=(
            SnapshotQueryFilter(
                AMOUNT_ID,
                SnapshotFilterOperator.GREATER_THAN,
                value="10",
            ),
        ),
        sorts=(
            ChartSort(
                ChartSortTargetType.MEASURE,
                MEASURE_ID,
                SnapshotSortDirection.DESCENDING,
            ),
        ),
        limit=10,
    )


def _user(role: SystemRole = SystemRole.ANALYST) -> User:
    return User(
        uuid4(),
        "analyst@example.com",
        "Analyst",
        role,
        AccountStatus.ACTIVE,
        None,
        NOW,
        NOW,
    )


class FakeAccessStore:
    def __init__(self, actor_id: UUID, allowed: bool = True) -> None:
        self.actor_id = actor_id
        self.allowed = allowed

    async def get_dataset_access(self, *, actor_id: UUID, dataset_id: UUID) -> DatasetAccess | None:
        if not self.allowed or actor_id != self.actor_id or dataset_id != DATASET_ID:
            return None
        return DatasetAccess(
            DATASET_ID,
            False,
            frozenset({DatasetPermission.VIEW_ROWS}),
        )


class FakeSnapshotStore:
    def __init__(self, context: ActiveSnapshotQueryContext | None) -> None:
        self.context = context

    async def get_active_context(self, dataset_id: UUID) -> ActiveSnapshotQueryContext | None:
        return self.context if dataset_id == DATASET_ID else None


class FakeChartStore:
    def __init__(self) -> None:
        self.chart: Chart | None = None
        self.audits: list[dict[str, Any]] = []

    async def create(self, **values: Any) -> Chart:
        create = values["value"]
        self.chart = Chart(
            id=values["chart_id"],
            dataset_id=create.dataset_id,
            owner_id=values["actor_id"],
            name=create.name,
            revision=1,
            definition=create.definition,
            created_at=values["occurred_at"],
            updated_at=values["occurred_at"],
        )
        return self.chart

    async def list(self, **values: Any) -> tuple[Chart, ...]:
        if self.chart is None:
            return ()
        owner_id = values["owner_id"]
        return (self.chart,) if owner_id is None or owner_id == self.chart.owner_id else ()

    async def get(self, chart_id: UUID) -> Chart | None:
        return self.chart if self.chart is not None and self.chart.id == chart_id else None

    async def update(self, **values: Any) -> Chart | None:
        if self.chart is None or self.chart.id != values["chart_id"]:
            return None
        if self.chart.revision != values["expected_revision"]:
            raise ChartRevisionConflictError
        self.chart = replace(
            self.chart,
            name=values["name"],
            revision=self.chart.revision + 1,
            definition=ChartDefinition.from_document(values["definition"]),
            updated_at=values["occurred_at"],
        )
        return self.chart

    async def record_query(self, **values: Any) -> None:
        self.audits.append(values)


class RecordingExecutor:
    async def query(self, **values: Any) -> Any:
        from datepipette.domain.charts import ChartQueryExecution

        prepared = values["prepared"]
        assert prepared.definition.filters[0].value == 10
        return ChartQueryExecution((), ({"ok": True},), False)


def test_chart_definition_round_trips_and_enforces_chart_shape() -> None:
    value = _definition()
    assert ChartDefinition.from_document(value.to_document()) == value
    legacy = replace(value, schema_version=1)
    assert ChartDefinition.from_document(legacy.to_document()) == legacy

    with pytest.raises(ValueError, match="version 1 does not support"):
        replace(
            legacy,
            dimensions=(ChartDimension(REGION_ID, ChartTimeBucket.DAY),),
        )

    with pytest.raises(ValueError, match="require no dimensions"):
        ChartDefinition(
            chart_type=ChartType.KPI,
            dimensions=(ChartDimension(REGION_ID),),
            measures=value.measures,
        )

    with pytest.raises(ValueError, match="between 1 and"):
        replace(value, measures=())
    with pytest.raises(ValueError, match="exactly one measure"):
        replace(value, chart_type=ChartType.PIE, measures=value.measures * 2)
    with pytest.raises(ValueError, match="outside the supported range"):
        replace(value, limit=0)
    with pytest.raises(ValueError, match="identifiers must be unique"):
        replace(value, measures=value.measures * 2)
    with pytest.raises(ValueError, match="must not be empty"):
        ChartMeasure(uuid4(), AMOUNT_ID, ChartAggregation.SUM, " ")


def test_chart_preparation_rejects_invalid_aggregate_and_sort_target() -> None:
    invalid_measure = ChartDefinition(
        chart_type=ChartType.BAR,
        dimensions=(ChartDimension(ORDER_ID),),
        measures=(ChartMeasure(MEASURE_ID, REGION_ID, ChartAggregation.SUM, "Bad sum"),),
    )
    with pytest.raises(InvalidChartDefinitionError, match="invalid for field"):
        prepare_chart_query(_context(), invalid_measure)

    with pytest.raises(InvalidChartDefinitionError, match="unknown result column"):
        prepare_chart_query(
            _context(),
            replace(
                _definition(),
                sorts=(
                    ChartSort(
                        ChartSortTargetType.MEASURE,
                        uuid4(),
                        SnapshotSortDirection.ASCENDING,
                    ),
                ),
            ),
        )

    with pytest.raises(InvalidChartDefinitionError, match="unknown dataset field"):
        prepare_chart_query(
            _context(),
            replace(
                _definition(),
                dimensions=(ChartDimension(uuid4()),),
            ),
        )

    json_field = SnapshotQueryField(
        uuid4(), "payload", "payload", "jsonb", True, True, "Payload", None
    )
    with pytest.raises(InvalidChartDefinitionError, match="cannot be chart dimensions"):
        prepare_chart_query(
            replace(_context(), fields=(*FIELDS, json_field)),
            replace(_definition(), dimensions=(ChartDimension(json_field.field_id),)),
        )

    with pytest.raises(InvalidChartDefinitionError, match="require a date or timestamp"):
        prepare_chart_query(
            _context(),
            replace(
                _definition(),
                dimensions=(ChartDimension(REGION_ID, ChartTimeBucket.DAY),),
            ),
        )

    date_field = SnapshotQueryField(
        uuid4(), "business_date", "business_date", "date", False, True, "Date", None
    )
    with pytest.raises(InvalidChartDefinitionError, match="Hourly"):
        prepare_chart_query(
            replace(_context(), fields=(*FIELDS, date_field)),
            replace(
                _definition(),
                dimensions=(ChartDimension(date_field.field_id, ChartTimeBucket.HOUR),),
            ),
        )


def test_duckdb_chart_query_groups_filters_sorts_and_limits(tmp_path: Path) -> None:
    pq.write_table(
        pa.table(
            {
                "region": ["North", "South", "North", "West"],
                "amount": [
                    Decimal("12.50"),
                    Decimal("20.00"),
                    Decimal("7.50"),
                    Decimal("30.00"),
                ],
                "order_id": [1, 2, 3, 4],
            },
            schema=pa.schema(
                [
                    pa.field("region", pa.string(), nullable=False),
                    pa.field("amount", pa.decimal128(12, 2)),
                    pa.field("order_id", pa.int64(), nullable=False),
                ]
            ),
        ),
        tmp_path / "snapshot.parquet",
    )
    executor = DuckDbLocalChartQueryExecutor(
        tmp_path,
        timeout_seconds=2,
        memory_limit_mb=64,
        threads=1,
        temporary_directory_limit_mb=64,
        maximum_concurrent_queries=1,
    )

    result = asyncio.run(
        executor.query(
            context=_context(),
            prepared=prepare_chart_query(_context(), _definition()),
            maximum_bytes=100_000,
        )
    )

    assert [item.role.value for item in result.columns] == ["dimension", "measure"]
    assert [row[result.columns[0].key] for row in result.rows] == ["West", "South", "North"]
    assert [row[result.columns[1].key] for row in result.rows] == ["30.00", "20.00", "12.50"]


def test_duckdb_chart_query_supports_all_measure_functions(tmp_path: Path) -> None:
    pq.write_table(
        pa.table(
            {
                "region": ["North", "North", "South"],
                "amount": [Decimal("10.00"), Decimal("20.00"), Decimal("5.00")],
                "order_id": [1, 2, 3],
            },
            schema=pa.schema(
                [
                    pa.field("region", pa.string(), nullable=False),
                    pa.field("amount", pa.decimal128(12, 2)),
                    pa.field("order_id", pa.int64(), nullable=False),
                ]
            ),
        ),
        tmp_path / "snapshot.parquet",
    )
    measures = (
        ChartMeasure(uuid4(), ORDER_ID, ChartAggregation.COUNT, "Orders"),
        ChartMeasure(uuid4(), ORDER_ID, ChartAggregation.COUNT_DISTINCT, "Unique orders"),
        ChartMeasure(uuid4(), AMOUNT_ID, ChartAggregation.AVERAGE, "Average"),
        ChartMeasure(uuid4(), AMOUNT_ID, ChartAggregation.MINIMUM, "Minimum"),
        ChartMeasure(uuid4(), AMOUNT_ID, ChartAggregation.MAXIMUM, "Maximum"),
    )
    definition = ChartDefinition(
        chart_type=ChartType.BAR,
        dimensions=(ChartDimension(REGION_ID),),
        measures=measures,
        limit=10,
    )
    executor = DuckDbLocalChartQueryExecutor(
        tmp_path,
        timeout_seconds=2,
        memory_limit_mb=64,
        threads=1,
        temporary_directory_limit_mb=64,
        maximum_concurrent_queries=1,
    )

    result = asyncio.run(
        executor.query(
            context=_context(),
            prepared=prepare_chart_query(_context(), definition),
            maximum_bytes=100_000,
        )
    )

    north = result.rows[0]
    assert north[result.columns[1].key] == 2
    assert north[result.columns[2].key] == 2
    assert north[result.columns[3].key] == 15.0
    assert north[result.columns[4].key] == "10.00"
    assert north[result.columns[5].key] == "20.00"


@pytest.mark.parametrize(
    ("bucket", "expected_first_bucket"),
    [
        (ChartTimeBucket.HOUR, "2026-02-01T01:00:00"),
        (ChartTimeBucket.DAY, "2026-02-01T00:00:00"),
        (ChartTimeBucket.WEEK, "2026-01-26T00:00:00"),
        (ChartTimeBucket.MONTH, "2026-02-01T00:00:00"),
        (ChartTimeBucket.QUARTER, "2026-01-01T00:00:00"),
        (ChartTimeBucket.YEAR, "2026-01-01T00:00:00"),
    ],
)
def test_duckdb_chart_query_uses_installation_timezone_for_time_buckets(
    tmp_path: Path,
    bucket: ChartTimeBucket,
    expected_first_bucket: str,
) -> None:
    pq.write_table(
        pa.table(
            {
                "occurred_at": [
                    datetime(2026, 1, 31, 20, 30, tzinfo=UTC),
                    datetime(2026, 2, 1, 0, 30, tzinfo=UTC),
                    datetime(2026, 4, 1, 0, 30, tzinfo=UTC),
                ],
                "amount": [Decimal("10.00"), Decimal("15.00"), Decimal("20.00")],
            },
            schema=pa.schema(
                [
                    pa.field("occurred_at", pa.timestamp("us", tz="UTC"), nullable=False),
                    pa.field("amount", pa.decimal128(12, 2), nullable=False),
                ]
            ),
        ),
        tmp_path / "snapshot.parquet",
    )
    definition = ChartDefinition(
        chart_type=ChartType.LINE,
        dimensions=(ChartDimension(OCCURRED_AT_ID, bucket),),
        measures=(ChartMeasure(MEASURE_ID, AMOUNT_ID, ChartAggregation.SUM, "Revenue"),),
        limit=100,
    )
    executor = DuckDbLocalChartQueryExecutor(
        tmp_path,
        timeout_seconds=2,
        memory_limit_mb=64,
        threads=1,
        temporary_directory_limit_mb=64,
        maximum_concurrent_queries=1,
        installation_timezone="Asia/Almaty",
    )

    result = asyncio.run(
        executor.query(
            context=_context(),
            prepared=prepare_chart_query(_context(), definition),
            maximum_bytes=100_000,
        )
    )

    dimension = result.columns[0]
    assert dimension.time_bucket is bucket
    assert dimension.timezone == "Asia/Almaty"
    assert dimension.data_type == "timestamp without time zone"
    assert result.rows[0][dimension.key] == expected_first_bucket


def test_duckdb_chart_hour_buckets_follow_daylight_saving_transition(tmp_path: Path) -> None:
    pq.write_table(
        pa.table(
            {
                "occurred_at": [
                    datetime(2026, 3, 8, 6, 30, tzinfo=UTC),
                    datetime(2026, 3, 8, 7, 30, tzinfo=UTC),
                ],
                "amount": [Decimal("10.00"), Decimal("20.00")],
            },
            schema=pa.schema(
                [
                    pa.field("occurred_at", pa.timestamp("us", tz="UTC"), nullable=False),
                    pa.field("amount", pa.decimal128(12, 2), nullable=False),
                ]
            ),
        ),
        tmp_path / "snapshot.parquet",
    )
    definition = ChartDefinition(
        chart_type=ChartType.LINE,
        dimensions=(ChartDimension(OCCURRED_AT_ID, ChartTimeBucket.HOUR),),
        measures=(ChartMeasure(MEASURE_ID, AMOUNT_ID, ChartAggregation.SUM, "Revenue"),),
    )
    executor = DuckDbLocalChartQueryExecutor(
        tmp_path,
        timeout_seconds=2,
        memory_limit_mb=64,
        threads=1,
        temporary_directory_limit_mb=64,
        maximum_concurrent_queries=1,
        installation_timezone="America/New_York",
    )

    result = asyncio.run(
        executor.query(
            context=_context(),
            prepared=prepare_chart_query(_context(), definition),
            maximum_bytes=100_000,
        )
    )

    dimension_key = result.columns[0].key
    assert [row[dimension_key] for row in result.rows] == [
        "2026-03-08T01:00:00",
        "2026-03-08T03:00:00",
    ]


def test_chart_service_create_update_query_and_rechecks_dataset_access() -> None:
    actor = _user()
    access = FakeAccessStore(actor.id)
    store = FakeChartStore()
    service = ChartService(
        store,
        FakeSnapshotStore(_context()),
        RecordingExecutor(),
        DatasetAccessAuthorizer(access),
        100_000,
    )

    async def exercise() -> None:
        chart = await service.create(
            actor=actor,
            value=ChartCreate(" Revenue by region ", DATASET_ID, _definition()),
            request_id="req-create",
        )
        assert chart.name == "Revenue by region"
        assert (await service.list(actor=actor, cursor=None, limit=10)).charts == (chart,)

        updated = await service.update(
            actor=actor,
            chart_id=chart.id,
            expected_revision=1,
            name="Revenue",
            definition=replace(_definition(), limit=5),
            request_id="req-update",
        )
        assert updated.revision == 2
        result = await service.query(actor=actor, chart_id=chart.id, request_id="req-query")
        assert result.rows == ({"ok": True},)
        assert store.audits[-1]["returned_rows"] == 1
        assert store.audits[-1]["actor_type"] is AccountType.HUMAN

        service_actor = replace(actor, account_type=AccountType.SERVICE)
        result = await service.query(
            actor=service_actor,
            chart_id=chart.id,
            request_id="req-service-query",
        )
        assert result.rows == ({"ok": True},)
        assert AccountType(store.audits[-1]["actor_type"]) is AccountType.SERVICE

        access.allowed = False
        with pytest.raises(ChartDatasetNotAvailableError):
            await service.query(actor=actor, chart_id=chart.id, request_id="req-denied")

        viewer = _user(SystemRole.VIEWER)
        shared = await service.query_dashboard_item(
            actor=viewer,
            chart_id=chart.id,
            dashboard_owner_id=actor.id,
            request_id="req-dashboard",
        )
        assert shared.rows == ({"ok": True},)
        assert store.audits[-1]["actor_id"] == viewer.id
        with pytest.raises(ChartNotFoundError):
            await service.query_dashboard_item(
                actor=viewer,
                chart_id=chart.id,
                dashboard_owner_id=uuid4(),
                request_id="req-wrong-owner",
            )

    asyncio.run(exercise())


def test_chart_service_conceals_other_owners_and_rejects_bad_cursor() -> None:
    actor = _user()
    other = _user()
    store = FakeChartStore()
    service = ChartService(
        store,
        FakeSnapshotStore(_context()),
        RecordingExecutor(),
        DatasetAccessAuthorizer(FakeAccessStore(actor.id)),
        100_000,
    )

    async def exercise() -> None:
        chart = await service.create(
            actor=actor,
            value=ChartCreate("Revenue", DATASET_ID, _definition()),
            request_id="req-create",
        )
        with pytest.raises(InvalidChartCursorError):
            await service.list(actor=actor, cursor="bad!", limit=10)
        with pytest.raises(ChartNotFoundError):
            await service.get(actor=other, chart_id=chart.id)
        viewer = replace(other, role=SystemRole.VIEWER)
        assert (await service.list(actor=viewer, cursor=None, limit=10)).charts == ()

    asyncio.run(exercise())


def test_viewer_cannot_create_chart_even_with_nominal_dataset_access() -> None:
    actor = _user(SystemRole.VIEWER)
    service = ChartService(
        FakeChartStore(),
        FakeSnapshotStore(_context()),
        RecordingExecutor(),
        DatasetAccessAuthorizer(FakeAccessStore(actor.id)),
        100_000,
    )

    async def exercise() -> None:
        with pytest.raises(ChartDatasetNotAvailableError):
            await service.create(
                actor=actor,
                value=ChartCreate("Revenue", DATASET_ID, _definition()),
                request_id="req-viewer",
            )

    asyncio.run(exercise())
