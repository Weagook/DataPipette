"""Application services for private charts and bounded aggregate queries."""

import base64
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Protocol
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from datapipette.application.access_control import (
    DatasetAccessAuthorizer,
    DatasetAccessDeniedError,
)
from datapipette.application.query_safety import (
    InteractiveQueryRuntime,
    QuerySafetyPolicyReader,
)
from datapipette.application.snapshot_queries import (
    DatasetSnapshotNotPublishedError,
    DatasetSnapshotUnavailableError,
    InvalidSnapshotQueryError,
    prepare_snapshot_filters,
    snapshot_type_family,
)
from datapipette.domain.access_control import DatasetPermission
from datapipette.domain.charts import (
    Chart,
    ChartAggregation,
    ChartDefinition,
    ChartDimension,
    ChartMeasure,
    ChartPreviewResult,
    ChartQueryExecution,
    ChartQueryResult,
    ChartResultColumnRole,
    ChartSort,
    ChartSortTargetType,
    ChartTimeBucket,
    ChartType,
)
from datapipette.domain.identity import AccountStatus, AccountType, SystemRole, User
from datapipette.domain.snapshot_queries import (
    ActiveSnapshotQueryContext,
    SnapshotFilterOperator,
    SnapshotQueryField,
    SnapshotQueryFilter,
    SnapshotSortDirection,
)

VALUE_FILTER_FAMILIES = frozenset({"text", "integer", "boolean"})
DATE_RANGE_FILTER_FAMILIES = frozenset({"date", "datetime"})
MAX_FILTER_VALUE_OPTIONS = 200


class ChartNotFoundError(Exception):
    pass


class ChartAlreadyExistsError(Exception):
    pass


class ChartDatasetNotAvailableError(Exception):
    pass


class ChartRevisionConflictError(Exception):
    pass


class InvalidChartCursorError(ValueError):
    pass


class InvalidChartDefinitionError(ValueError):
    pass


class ChartQueryTimeoutError(Exception):
    pass


class ChartQueryCapacityExceededError(Exception):
    pass


class ChartQueryUnavailableError(Exception):
    pass


class ChartQueryResultTooLargeError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class ChartCreate:
    name: str
    dataset_id: UUID
    definition: ChartDefinition


@dataclass(frozen=True, slots=True)
class ChartPage:
    charts: tuple[Chart, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class PreparedChartQuery:
    definition: ChartDefinition
    dimension_fields: tuple[SnapshotQueryField, ...]
    measure_fields: tuple[SnapshotQueryField, ...]
    context_filters: tuple[SnapshotQueryFilter, ...] = ()


@dataclass(frozen=True, slots=True)
class ChartContextFilter:
    """A restriction applied on top of a saved chart, such as a dashboard filter choice."""

    field_id: UUID
    values: tuple[Any, ...] = ()
    date_from: date | None = None
    date_to: date | None = None


@dataclass(frozen=True, slots=True)
class ChartFieldValues:
    values: tuple[Any, ...]
    truncated: bool


class ChartStore(Protocol):
    async def create(
        self,
        *,
        chart_id: UUID,
        actor_id: UUID,
        value: ChartCreate,
        definition: dict[str, object],
        field_ids: frozenset[UUID],
        occurred_at: datetime,
        request_id: str,
    ) -> Chart: ...

    async def list(
        self,
        *,
        owner_id: UUID | None,
        after_created_at: datetime | None,
        after_id: UUID | None,
        limit: int,
    ) -> tuple[Chart, ...]: ...

    async def get(self, chart_id: UUID) -> Chart | None: ...

    async def update(
        self,
        *,
        chart_id: UUID,
        actor_id: UUID,
        expected_revision: int,
        name: str,
        definition: dict[str, object],
        field_ids: frozenset[UUID],
        occurred_at: datetime,
        request_id: str,
    ) -> Chart | None: ...

    async def record_preview(
        self,
        *,
        dataset_id: UUID,
        context: ActiveSnapshotQueryContext,
        actor_id: UUID,
        policy_revision: int,
        returned_rows: int,
        truncated_by_bytes: bool,
        error_code: str | None,
        request_id: str,
        occurred_at: datetime,
    ) -> None: ...

    async def record_query(
        self,
        *,
        chart: Chart,
        context: ActiveSnapshotQueryContext,
        actor_id: UUID,
        actor_type: AccountType,
        policy_revision: int,
        returned_rows: int,
        truncated_by_bytes: bool,
        error_code: str | None,
        request_id: str,
        occurred_at: datetime,
    ) -> None: ...


class ActiveSnapshotContextStore(Protocol):
    async def get_active_context(self, dataset_id: UUID) -> ActiveSnapshotQueryContext | None: ...


class ChartQueryExecutor(Protocol):
    async def query(
        self,
        *,
        context: ActiveSnapshotQueryContext,
        prepared: PreparedChartQuery,
        runtime: InteractiveQueryRuntime,
    ) -> ChartQueryExecution: ...


def _name(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise InvalidChartDefinitionError("Chart name must not be empty")
    if len(normalized) > 100:
        raise InvalidChartDefinitionError("Chart name must contain at most 100 characters")
    return normalized


def _encode_cursor(chart: Chart) -> str:
    value = f"{chart.created_at.isoformat()}|{chart.id}".encode()
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode_cursor(value: str | None) -> tuple[datetime | None, UUID | None]:
    if value is None:
        return None, None
    try:
        padding = "=" * (-len(value) % 4)
        decoded = base64.b64decode(value + padding, altchars=b"-_", validate=True).decode()
        timestamp, identifier = decoded.split("|", maxsplit=1)
        created_at = datetime.fromisoformat(timestamp)
        if created_at.tzinfo is None:
            raise ValueError
        return created_at, UUID(identifier)
    except (UnicodeDecodeError, ValueError) as exc:
        raise InvalidChartCursorError from exc


def _require_chart_owner(actor: User, chart: Chart) -> None:
    if actor.status is not AccountStatus.ACTIVE:
        raise ChartNotFoundError
    if actor.role is SystemRole.ADMIN:
        return
    if actor.role is SystemRole.ANALYST and chart.owner_id == actor.id:
        return
    raise ChartNotFoundError


def _supports_aggregation(field: SnapshotQueryField, aggregation: ChartAggregation) -> bool:
    try:
        family = snapshot_type_family(field.data_type)
    except InvalidSnapshotQueryError:
        return False
    if aggregation in {ChartAggregation.COUNT, ChartAggregation.COUNT_DISTINCT}:
        return family != "binary"
    if aggregation in {ChartAggregation.SUM, ChartAggregation.AVERAGE}:
        return family in {"integer", "number"}
    if aggregation in {ChartAggregation.MINIMUM, ChartAggregation.MAXIMUM}:
        return family in {"integer", "number", "date", "datetime", "time", "text"}
    return False


def prepare_chart_query(
    context: ActiveSnapshotQueryContext,
    definition: ChartDefinition,
) -> PreparedChartQuery:
    fields = {field.field_id: field for field in context.fields}
    try:
        dimensions = tuple(fields[item.field_id] for item in definition.dimensions)
        measure_fields = tuple(fields[item.field_id] for item in definition.measures)
    except KeyError as exc:
        raise InvalidChartDefinitionError("Chart references an unknown dataset field") from exc
    for dimension, field in zip(definition.dimensions, dimensions, strict=True):
        try:
            family = snapshot_type_family(field.data_type)
        except InvalidSnapshotQueryError as exc:
            raise InvalidChartDefinitionError(
                "Chart dimension has an unsupported field type"
            ) from exc
        if family in {"binary", "json"}:
            raise InvalidChartDefinitionError("Binary and JSON fields cannot be chart dimensions")
        if dimension.time_bucket is not None and family not in {"date", "datetime"}:
            raise InvalidChartDefinitionError(
                "Chart time buckets require a date or timestamp dimension"
            )
        if dimension.time_bucket is ChartTimeBucket.HOUR and family == "date":
            raise InvalidChartDefinitionError("Hourly chart buckets require a timestamp dimension")
    for measure, field in zip(definition.measures, measure_fields, strict=True):
        if not _supports_aggregation(field, measure.aggregation):
            raise InvalidChartDefinitionError(
                f"Aggregation {measure.aggregation.value} is invalid for field {measure.field_id}"
            )
    dimension_ids = {item.field_id for item in definition.dimensions}
    measure_ids = {item.id for item in definition.measures}
    for item in definition.sorts:
        expected = (
            dimension_ids if item.target_type is ChartSortTargetType.DIMENSION else measure_ids
        )
        if item.target_id not in expected:
            raise InvalidChartDefinitionError("Chart sort references an unknown result column")
    try:
        normalized_filters = prepare_snapshot_filters(context, definition.filters)
    except InvalidSnapshotQueryError as exc:
        raise InvalidChartDefinitionError(str(exc)) from exc
    return PreparedChartQuery(
        definition=replace(definition, filters=normalized_filters),
        dimension_fields=dimensions,
        measure_fields=measure_fields,
    )


def _family(field: SnapshotQueryField) -> str | None:
    try:
        return snapshot_type_family(field.data_type)
    except InvalidSnapshotQueryError:
        return None


class ChartService:
    def __init__(
        self,
        store: ChartStore,
        snapshot_store: ActiveSnapshotContextStore,
        executor: ChartQueryExecutor,
        authorizer: DatasetAccessAuthorizer,
        policy_store: QuerySafetyPolicyReader,
        installation_timezone: str = "UTC",
    ) -> None:
        self._store = store
        self._snapshot_store = snapshot_store
        self._executor = executor
        self._authorizer = authorizer
        self._policy_store = policy_store
        self._timezone = ZoneInfo(installation_timezone)

    async def create(self, *, actor: User, value: ChartCreate, request_id: str) -> Chart:
        if actor.status is not AccountStatus.ACTIVE or actor.role not in {
            SystemRole.ADMIN,
            SystemRole.ANALYST,
        }:
            raise ChartDatasetNotAvailableError
        context = await self._authorized_context(actor=actor, dataset_id=value.dataset_id)
        prepare_chart_query(context, value.definition)
        normalized = ChartCreate(
            name=_name(value.name),
            dataset_id=value.dataset_id,
            definition=value.definition,
        )
        return await self._store.create(
            chart_id=uuid4(),
            actor_id=actor.id,
            value=normalized,
            definition=value.definition.to_document(),
            field_ids=value.definition.referenced_field_ids,
            occurred_at=datetime.now(UTC),
            request_id=request_id,
        )

    async def list(
        self,
        *,
        actor: User,
        cursor: str | None,
        limit: int,
    ) -> ChartPage:
        if actor.status is not AccountStatus.ACTIVE or actor.role is SystemRole.VIEWER:
            return ChartPage((), None)
        after_created_at, after_id = _decode_cursor(cursor)
        items = await self._store.list(
            owner_id=None if actor.role is SystemRole.ADMIN else actor.id,
            after_created_at=after_created_at,
            after_id=after_id,
            limit=limit + 1,
        )
        visible = items[:limit]
        return ChartPage(
            charts=visible,
            next_cursor=_encode_cursor(visible[-1]) if len(items) > limit else None,
        )

    async def get(self, *, actor: User, chart_id: UUID) -> Chart:
        chart = await self._store.get(chart_id)
        if chart is None:
            raise ChartNotFoundError
        _require_chart_owner(actor, chart)
        return chart

    async def update(
        self,
        *,
        actor: User,
        chart_id: UUID,
        expected_revision: int,
        name: str,
        definition: ChartDefinition,
        request_id: str,
    ) -> Chart:
        current = await self.get(actor=actor, chart_id=chart_id)
        context = await self._authorized_context(actor=actor, dataset_id=current.dataset_id)
        prepare_chart_query(context, definition)
        updated = await self._store.update(
            chart_id=chart_id,
            actor_id=actor.id,
            expected_revision=expected_revision,
            name=_name(name),
            definition=definition.to_document(),
            field_ids=definition.referenced_field_ids,
            occurred_at=datetime.now(UTC),
            request_id=request_id,
        )
        if updated is None:
            raise ChartNotFoundError
        return updated

    async def preview(
        self,
        *,
        actor: User,
        dataset_id: UUID,
        definition: ChartDefinition,
        request_id: str,
    ) -> ChartPreviewResult:
        """Run an unsaved definition with exactly the rights and limits of saving it."""
        if actor.status is not AccountStatus.ACTIVE or actor.role not in {
            SystemRole.ADMIN,
            SystemRole.ANALYST,
        }:
            raise ChartDatasetNotAvailableError
        context = await self._authorized_context(actor=actor, dataset_id=dataset_id)
        prepared = prepare_chart_query(context, definition)
        managed_policy = await self._policy_store.get()
        runtime = InteractiveQueryRuntime.from_policy(managed_policy.policy)

        async def record(returned_rows: int, truncated: bool, error_code: str | None) -> None:
            await self._store.record_preview(
                dataset_id=dataset_id,
                context=context,
                actor_id=actor.id,
                policy_revision=managed_policy.revision,
                returned_rows=returned_rows,
                truncated_by_bytes=truncated,
                error_code=error_code,
                request_id=request_id,
                occurred_at=datetime.now(UTC),
            )

        try:
            execution = await self._executor.query(
                context=context,
                prepared=prepared,
                runtime=runtime,
            )
        except (
            ChartQueryCapacityExceededError,
            ChartQueryTimeoutError,
            ChartQueryResultTooLargeError,
            ChartQueryUnavailableError,
        ) as exc:
            await record(
                0,
                False,
                "interactive_query_capacity_exceeded"
                if isinstance(exc, ChartQueryCapacityExceededError)
                else "execution_failed",
            )
            raise
        await record(len(execution.rows), execution.truncated_by_bytes, None)
        return ChartPreviewResult(
            policy_revision=managed_policy.revision,
            chart_type=definition.chart_type,
            dataset_id=dataset_id,
            snapshot_id=context.snapshot_id,
            snapshot_activated_at=context.activated_at,
            refresh_state=context.refresh_state,
            is_stale=context.is_stale,
            columns=execution.columns,
            rows=execution.rows,
            truncated_by_bytes=execution.truncated_by_bytes,
        )

    async def query(
        self,
        *,
        actor: User,
        chart_id: UUID,
        request_id: str,
        context_filters: tuple[ChartContextFilter, ...] = (),
    ) -> ChartQueryResult:
        chart = await self.get(actor=actor, chart_id=chart_id)
        context = await self._authorized_context(actor=actor, dataset_id=chart.dataset_id)
        return await self._execute(
            actor=actor,
            chart=chart,
            context=context,
            request_id=request_id,
            context_filters=context_filters,
        )

    async def query_dashboard_item(
        self,
        *,
        actor: User,
        chart_id: UUID,
        dashboard_owner_id: UUID,
        request_id: str,
        context_filters: tuple[ChartContextFilter, ...] = (),
    ) -> ChartQueryResult:
        chart, context = await self._dashboard_context(
            actor=actor, chart_id=chart_id, dashboard_owner_id=dashboard_owner_id
        )
        return await self._execute(
            actor=actor,
            chart=chart,
            context=context,
            request_id=request_id,
            context_filters=context_filters,
        )

    async def field_families(self, chart_id: UUID) -> dict[UUID, str]:
        """Return the type family of every field in a chart's active published contract."""

        chart = await self._store.get(chart_id)
        if chart is None:
            raise ChartNotFoundError
        context = await self._active_context(chart.dataset_id)
        families: dict[UUID, str] = {}
        for field in context.fields:
            try:
                families[field.field_id] = snapshot_type_family(field.data_type)
            except InvalidSnapshotQueryError:
                continue
        return families

    async def field_values(
        self,
        *,
        actor: User,
        chart_id: UUID,
        field_id: UUID,
        dashboard_owner_id: UUID,
        direct_access: bool,
    ) -> ChartFieldValues:
        """List the distinct values of one chart dataset field for a dashboard filter."""

        if direct_access:
            chart = await self.get(actor=actor, chart_id=chart_id)
            if chart.owner_id != dashboard_owner_id:
                raise ChartNotFoundError
            context = await self._authorized_context(actor=actor, dataset_id=chart.dataset_id)
        else:
            chart, context = await self._dashboard_context(
                actor=actor, chart_id=chart_id, dashboard_owner_id=dashboard_owner_id
            )
        field = next((item for item in context.fields if item.field_id == field_id), None)
        if field is None or _family(field) not in VALUE_FILTER_FAMILIES:
            raise InvalidChartDefinitionError("The filter field is not available for value lists")
        definition = ChartDefinition(
            chart_type=ChartType.BAR,
            dimensions=(ChartDimension(field_id),),
            measures=(ChartMeasure(uuid4(), field_id, ChartAggregation.COUNT, "Rows"),),
            sorts=(
                ChartSort(ChartSortTargetType.DIMENSION, field_id, SnapshotSortDirection.ASCENDING),
            ),
            limit=MAX_FILTER_VALUE_OPTIONS + 1,
        )
        managed_policy = await self._policy_store.get()
        execution = await self._executor.query(
            context=context,
            prepared=prepare_chart_query(context, definition),
            runtime=InteractiveQueryRuntime.from_policy(managed_policy.policy),
        )
        key = next(
            column.key
            for column in execution.columns
            if column.role is ChartResultColumnRole.DIMENSION
        )
        values = tuple(row[key] for row in execution.rows if row[key] is not None)
        return ChartFieldValues(
            values=values[:MAX_FILTER_VALUE_OPTIONS],
            truncated=len(values) > MAX_FILTER_VALUE_OPTIONS,
        )

    async def _dashboard_context(
        self,
        *,
        actor: User,
        chart_id: UUID,
        dashboard_owner_id: UUID,
    ) -> tuple[Chart, ActiveSnapshotQueryContext]:
        if actor.status is not AccountStatus.ACTIVE:
            raise ChartNotFoundError
        chart = await self._store.get(chart_id)
        if chart is None or chart.owner_id != dashboard_owner_id:
            raise ChartNotFoundError
        return chart, await self._active_context(chart.dataset_id)

    async def _active_context(self, dataset_id: UUID) -> ActiveSnapshotQueryContext:
        try:
            context = await self._snapshot_store.get_active_context(dataset_id)
        except (DatasetSnapshotNotPublishedError, DatasetSnapshotUnavailableError) as exc:
            raise ChartDatasetNotAvailableError from exc
        if context is None:
            raise ChartDatasetNotAvailableError
        return context

    def _context_filters(
        self,
        context: ActiveSnapshotQueryContext,
        filters: tuple[ChartContextFilter, ...],
    ) -> tuple[SnapshotQueryFilter, ...]:
        fields = {field.field_id: field for field in context.fields}
        compiled: list[SnapshotQueryFilter] = []
        for item in filters:
            field = fields.get(item.field_id)
            family = _family(field) if field is not None else None
            if item.values:
                if family not in VALUE_FILTER_FAMILIES:
                    raise InvalidChartDefinitionError("A dashboard filter field is unavailable")
                compiled.append(
                    SnapshotQueryFilter(
                        item.field_id, SnapshotFilterOperator.IN, values=item.values
                    )
                )
                continue
            if family not in DATE_RANGE_FILTER_FAMILIES:
                raise InvalidChartDefinitionError("A dashboard filter field is unavailable")
            if item.date_from is not None:
                compiled.append(
                    SnapshotQueryFilter(
                        item.field_id,
                        SnapshotFilterOperator.GREATER_THAN_OR_EQUAL,
                        value=self._date_bound(item.date_from, family),
                    )
                )
            if item.date_to is not None:
                compiled.append(
                    SnapshotQueryFilter(
                        item.field_id,
                        SnapshotFilterOperator.LESS_THAN_OR_EQUAL
                        if family == "date"
                        else SnapshotFilterOperator.LESS_THAN,
                        value=self._date_bound(
                            item.date_to if family == "date" else item.date_to + timedelta(days=1),
                            family,
                        ),
                    )
                )
        try:
            return prepare_snapshot_filters(context, tuple(compiled))
        except InvalidSnapshotQueryError as exc:
            raise InvalidChartDefinitionError(str(exc)) from exc

    def _date_bound(self, value: date, family: str | None) -> str:
        if family == "date":
            return value.isoformat()
        return datetime.combine(value, time.min, tzinfo=self._timezone).isoformat()

    async def _execute(
        self,
        *,
        actor: User,
        chart: Chart,
        context: ActiveSnapshotQueryContext,
        request_id: str,
        context_filters: tuple[ChartContextFilter, ...] = (),
    ) -> ChartQueryResult:
        prepared = prepare_chart_query(context, chart.definition)
        if context_filters:
            prepared = replace(
                prepared, context_filters=self._context_filters(context, context_filters)
            )
        managed_policy = await self._policy_store.get()
        runtime = InteractiveQueryRuntime.from_policy(managed_policy.policy)
        try:
            execution = await self._executor.query(
                context=context,
                prepared=prepared,
                runtime=runtime,
            )
        except (
            ChartQueryCapacityExceededError,
            ChartQueryTimeoutError,
            ChartQueryResultTooLargeError,
            ChartQueryUnavailableError,
        ) as exc:
            await self._record_query(
                chart=chart,
                context=context,
                actor=actor,
                policy_revision=managed_policy.revision,
                returned_rows=0,
                truncated_by_bytes=False,
                error_code=(
                    "interactive_query_capacity_exceeded"
                    if isinstance(exc, ChartQueryCapacityExceededError)
                    else "execution_failed"
                ),
                request_id=request_id,
            )
            raise
        await self._record_query(
            chart=chart,
            context=context,
            actor=actor,
            policy_revision=managed_policy.revision,
            returned_rows=len(execution.rows),
            truncated_by_bytes=execution.truncated_by_bytes,
            error_code=None,
            request_id=request_id,
        )
        return ChartQueryResult(
            policy_revision=managed_policy.revision,
            chart_id=chart.id,
            chart_name=chart.name,
            chart_type=chart.definition.chart_type,
            chart_revision=chart.revision,
            dataset_id=chart.dataset_id,
            snapshot_id=context.snapshot_id,
            snapshot_activated_at=context.activated_at,
            refresh_state=context.refresh_state,
            last_refresh_error=context.last_refresh_error,
            last_refresh_error_at=context.last_refresh_error_at,
            is_stale=context.is_stale,
            columns=execution.columns,
            rows=execution.rows,
            truncated_by_bytes=execution.truncated_by_bytes,
        )

    async def _authorized_context(
        self,
        *,
        actor: User,
        dataset_id: UUID,
    ) -> ActiveSnapshotQueryContext:
        try:
            await self._authorizer.require(
                actor=actor,
                dataset_id=dataset_id,
                permissions=frozenset({DatasetPermission.VIEW_ROWS}),
            )
        except DatasetAccessDeniedError as exc:
            raise ChartDatasetNotAvailableError from exc
        return await self._active_context(dataset_id)

    async def _record_query(
        self,
        *,
        chart: Chart,
        context: ActiveSnapshotQueryContext,
        actor: User,
        policy_revision: int,
        returned_rows: int,
        truncated_by_bytes: bool,
        error_code: str | None,
        request_id: str,
    ) -> None:
        await self._store.record_query(
            chart=chart,
            context=context,
            actor_id=actor.id,
            actor_type=actor.account_type,
            policy_revision=policy_revision,
            returned_rows=returned_rows,
            truncated_by_bytes=truncated_by_bytes,
            error_code=error_code,
            request_id=request_id,
            occurred_at=datetime.now(UTC),
        )
