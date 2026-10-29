"""Application services for private dashboard layouts."""

import base64
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from datapipette.application.charts import (
    DATE_RANGE_FILTER_FAMILIES,
    VALUE_FILTER_FAMILIES,
    ChartContextFilter,
    ChartDatasetNotAvailableError,
    ChartFieldValues,
    ChartNotFoundError,
)
from datapipette.domain.access_control import GrantPrincipalType
from datapipette.domain.charts import ChartQueryResult
from datapipette.domain.dashboards import (
    MAX_DASHBOARD_FILTERS,
    Dashboard,
    DashboardDefinition,
    DashboardFilterKind,
    DashboardFilterSelection,
    DashboardGrant,
)
from datapipette.domain.identity import AccountStatus, SystemRole, User


class DashboardNotFoundError(Exception):
    pass


class DashboardAlreadyExistsError(Exception):
    pass


class DashboardRevisionConflictError(Exception):
    pass


class DashboardItemNotFoundError(Exception):
    pass


class DashboardGrantNotFoundError(Exception):
    pass


class DashboardGrantPrincipalNotFoundError(Exception):
    pass


class InvalidDashboardCursorError(ValueError):
    pass


class InvalidDashboardDefinitionError(ValueError):
    pass


class DashboardFilterNotFoundError(Exception):
    pass


class InvalidDashboardFilterSelectionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DashboardFilterValues:
    dashboard_id: UUID
    filter_id: UUID
    values: tuple[object, ...]
    truncated: bool


@dataclass(frozen=True, slots=True)
class DashboardCreate:
    name: str
    definition: DashboardDefinition


@dataclass(frozen=True, slots=True)
class DashboardPage:
    dashboards: tuple[Dashboard, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class DashboardItemQueryResult:
    dashboard_id: UUID
    dashboard_revision: int
    item_id: UUID
    chart_result: ChartQueryResult


class SavedChartQueryService(Protocol):
    async def query(
        self,
        *,
        actor: User,
        chart_id: UUID,
        request_id: str,
        context_filters: tuple[ChartContextFilter, ...] = (),
    ) -> ChartQueryResult: ...

    async def query_dashboard_item(
        self,
        *,
        actor: User,
        chart_id: UUID,
        dashboard_owner_id: UUID,
        request_id: str,
        context_filters: tuple[ChartContextFilter, ...] = (),
    ) -> ChartQueryResult: ...

    async def field_families(self, chart_id: UUID) -> dict[UUID, str]: ...

    async def field_values(
        self,
        *,
        actor: User,
        chart_id: UUID,
        field_id: UUID,
        dashboard_owner_id: UUID,
        direct_access: bool,
    ) -> ChartFieldValues: ...


class DashboardStore(Protocol):
    async def create(
        self,
        *,
        dashboard_id: UUID,
        actor_id: UUID,
        value: DashboardCreate,
        definition: dict[str, object],
        occurred_at: datetime,
        request_id: str,
    ) -> Dashboard: ...

    async def list(
        self,
        *,
        actor_id: UUID,
        include_all: bool,
        after_created_at: datetime | None,
        after_id: UUID | None,
        limit: int,
    ) -> tuple[Dashboard, ...]: ...

    async def get(self, dashboard_id: UUID) -> Dashboard | None: ...

    async def has_view_access(self, *, dashboard_id: UUID, actor_id: UUID) -> bool: ...

    async def list_grants(self, dashboard_id: UUID) -> tuple[DashboardGrant, ...] | None: ...

    async def create_grant(
        self,
        *,
        grant_id: UUID,
        dashboard_id: UUID,
        principal_type: GrantPrincipalType,
        principal_id: UUID,
        actor_id: UUID,
        occurred_at: datetime,
        request_id: str,
    ) -> DashboardGrant | None: ...

    async def revoke_grant(
        self,
        *,
        dashboard_id: UUID,
        grant_id: UUID,
        actor_id: UUID,
        occurred_at: datetime,
        request_id: str,
    ) -> bool | None: ...

    async def resolve_chart_owners(self, chart_ids: frozenset[UUID]) -> dict[UUID, UUID]: ...

    async def update(
        self,
        *,
        dashboard_id: UUID,
        actor_id: UUID,
        expected_revision: int,
        name: str,
        definition: dict[str, object],
        occurred_at: datetime,
        request_id: str,
    ) -> Dashboard | None: ...


def _name(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise InvalidDashboardDefinitionError("Dashboard name must not be empty")
    if len(normalized) > 100:
        raise InvalidDashboardDefinitionError("Dashboard name must contain at most 100 characters")
    return normalized


def _encode_cursor(dashboard: Dashboard) -> str:
    value = f"{dashboard.created_at.isoformat()}|{dashboard.id}".encode()
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
        raise InvalidDashboardCursorError from exc


def _context_filters(
    definition: DashboardDefinition,
    item_id: UUID,
    selections: tuple[DashboardFilterSelection, ...],
) -> tuple[ChartContextFilter, ...]:
    if len(selections) > MAX_DASHBOARD_FILTERS:
        raise InvalidDashboardFilterSelectionError("Too many dashboard filter selections")
    if len({item.filter_id for item in selections}) != len(selections):
        raise InvalidDashboardFilterSelectionError("Each dashboard filter may be selected once")
    constraints: list[ChartContextFilter] = []
    for selection in selections:
        dashboard_filter = definition.filter(selection.filter_id)
        if dashboard_filter is None:
            raise InvalidDashboardFilterSelectionError("The dashboard has no such filter")
        if selection.is_empty:
            continue
        is_values = dashboard_filter.kind is DashboardFilterKind.VALUES
        if is_values != bool(selection.values):
            raise InvalidDashboardFilterSelectionError(
                f"Filter {dashboard_filter.label!r} expects "
                + ("values" if is_values else "a date range")
            )
        target = next(
            (value for value in dashboard_filter.targets if value.item_id == item_id), None
        )
        if target is not None:
            constraints.append(
                ChartContextFilter(
                    field_id=target.field_id,
                    values=selection.values,
                    date_from=selection.date_from,
                    date_to=selection.date_to,
                )
            )
    return tuple(constraints)


def _can_manage(actor: User, dashboard: Dashboard) -> bool:
    if actor.status is not AccountStatus.ACTIVE:
        return False
    if actor.role is SystemRole.ADMIN:
        return True
    return actor.role is SystemRole.ANALYST and dashboard.owner_id == actor.id


class DashboardService:
    def __init__(
        self,
        store: DashboardStore,
        chart_queries: SavedChartQueryService,
    ) -> None:
        self._store = store
        self._chart_queries = chart_queries

    async def create(
        self,
        *,
        actor: User,
        value: DashboardCreate,
        request_id: str,
    ) -> Dashboard:
        if actor.status is not AccountStatus.ACTIVE or actor.role not in {
            SystemRole.ADMIN,
            SystemRole.ANALYST,
        }:
            raise DashboardNotFoundError
        await self._validate_references(owner_id=actor.id, definition=value.definition)
        normalized = DashboardCreate(_name(value.name), value.definition)
        return await self._store.create(
            dashboard_id=uuid4(),
            actor_id=actor.id,
            value=normalized,
            definition=value.definition.to_document(),
            occurred_at=datetime.now(UTC),
            request_id=request_id,
        )

    async def list(
        self,
        *,
        actor: User,
        cursor: str | None,
        limit: int,
    ) -> DashboardPage:
        if actor.status is not AccountStatus.ACTIVE:
            return DashboardPage((), None)
        after_created_at, after_id = _decode_cursor(cursor)
        items = await self._store.list(
            actor_id=actor.id,
            include_all=actor.role is SystemRole.ADMIN,
            after_created_at=after_created_at,
            after_id=after_id,
            limit=limit + 1,
        )
        visible = items[:limit]
        return DashboardPage(
            dashboards=visible,
            next_cursor=_encode_cursor(visible[-1]) if len(items) > limit else None,
        )

    async def get(self, *, actor: User, dashboard_id: UUID) -> Dashboard:
        if actor.status is not AccountStatus.ACTIVE:
            raise DashboardNotFoundError
        dashboard = await self._store.get(dashboard_id)
        if dashboard is None:
            raise DashboardNotFoundError
        if not _can_manage(actor, dashboard) and not await self._store.has_view_access(
            dashboard_id=dashboard.id, actor_id=actor.id
        ):
            raise DashboardNotFoundError
        return dashboard

    async def _get_managed(self, *, actor: User, dashboard_id: UUID) -> Dashboard:
        dashboard = await self._store.get(dashboard_id)
        if dashboard is None or not _can_manage(actor, dashboard):
            raise DashboardNotFoundError
        return dashboard

    async def update(
        self,
        *,
        actor: User,
        dashboard_id: UUID,
        expected_revision: int,
        name: str,
        definition: DashboardDefinition,
        request_id: str,
    ) -> Dashboard:
        current = await self._get_managed(actor=actor, dashboard_id=dashboard_id)
        await self._validate_references(owner_id=current.owner_id, definition=definition)
        updated = await self._store.update(
            dashboard_id=dashboard_id,
            actor_id=actor.id,
            expected_revision=expected_revision,
            name=_name(name),
            definition=definition.to_document(),
            occurred_at=datetime.now(UTC),
            request_id=request_id,
        )
        if updated is None:
            raise DashboardNotFoundError
        return updated

    async def query_item(
        self,
        *,
        actor: User,
        dashboard_id: UUID,
        item_id: UUID,
        request_id: str,
        selections: tuple[DashboardFilterSelection, ...] = (),
    ) -> DashboardItemQueryResult:
        dashboard = await self.get(actor=actor, dashboard_id=dashboard_id)
        item = next((value for value in dashboard.definition.items if value.id == item_id), None)
        if item is None:
            raise DashboardItemNotFoundError
        context_filters = _context_filters(dashboard.definition, item.id, selections)
        if _can_manage(actor, dashboard):
            chart_result = await self._chart_queries.query(
                actor=actor,
                chart_id=item.chart_id,
                request_id=request_id,
                context_filters=context_filters,
            )
        else:
            chart_result = await self._chart_queries.query_dashboard_item(
                actor=actor,
                chart_id=item.chart_id,
                dashboard_owner_id=dashboard.owner_id,
                request_id=request_id,
                context_filters=context_filters,
            )
        return DashboardItemQueryResult(
            dashboard_id=dashboard.id,
            dashboard_revision=dashboard.revision,
            item_id=item.id,
            chart_result=chart_result,
        )

    async def list_filter_values(
        self,
        *,
        actor: User,
        dashboard_id: UUID,
        filter_id: UUID,
    ) -> DashboardFilterValues:
        dashboard = await self.get(actor=actor, dashboard_id=dashboard_id)
        dashboard_filter = dashboard.definition.filter(filter_id)
        if dashboard_filter is None or dashboard_filter.kind is not DashboardFilterKind.VALUES:
            raise DashboardFilterNotFoundError
        charts = {item.id: item.chart_id for item in dashboard.definition.items}
        collected: set[object] = set()
        truncated = False
        for target in dashboard_filter.targets:
            try:
                result = await self._chart_queries.field_values(
                    actor=actor,
                    chart_id=charts[target.item_id],
                    field_id=target.field_id,
                    dashboard_owner_id=dashboard.owner_id,
                    direct_access=_can_manage(actor, dashboard),
                )
            except (ChartNotFoundError, ChartDatasetNotAvailableError):
                continue
            collected.update(result.values)
            truncated = truncated or result.truncated
        values = tuple(sorted(collected, key=lambda value: (type(value).__name__, value)))
        limit = 200
        return DashboardFilterValues(
            dashboard_id=dashboard.id,
            filter_id=filter_id,
            values=values[:limit],
            truncated=truncated or len(values) > limit,
        )

    async def list_grants(self, *, actor: User, dashboard_id: UUID) -> tuple[DashboardGrant, ...]:
        await self._get_managed(actor=actor, dashboard_id=dashboard_id)
        grants = await self._store.list_grants(dashboard_id)
        if grants is None:
            raise DashboardNotFoundError
        return grants

    async def create_grant(
        self,
        *,
        actor: User,
        dashboard_id: UUID,
        principal_type: GrantPrincipalType,
        principal_id: UUID,
        request_id: str,
    ) -> DashboardGrant:
        await self._get_managed(actor=actor, dashboard_id=dashboard_id)
        grant = await self._store.create_grant(
            grant_id=uuid4(),
            dashboard_id=dashboard_id,
            principal_type=principal_type,
            principal_id=principal_id,
            actor_id=actor.id,
            occurred_at=datetime.now(UTC),
            request_id=request_id,
        )
        if grant is None:
            raise DashboardGrantPrincipalNotFoundError
        return grant

    async def revoke_grant(
        self,
        *,
        actor: User,
        dashboard_id: UUID,
        grant_id: UUID,
        request_id: str,
    ) -> None:
        await self._get_managed(actor=actor, dashboard_id=dashboard_id)
        result = await self._store.revoke_grant(
            dashboard_id=dashboard_id,
            grant_id=grant_id,
            actor_id=actor.id,
            occurred_at=datetime.now(UTC),
            request_id=request_id,
        )
        if result is None:
            raise DashboardNotFoundError
        if not result:
            raise DashboardGrantNotFoundError

    async def _validate_references(
        self,
        *,
        owner_id: UUID,
        definition: DashboardDefinition,
    ) -> None:
        owners = await self._store.resolve_chart_owners(definition.chart_ids)
        if set(owners) != set(definition.chart_ids) or any(
            chart_owner_id != owner_id for chart_owner_id in owners.values()
        ):
            raise InvalidDashboardDefinitionError(
                "One or more dashboard chart references are unavailable"
            )
        charts = {item.id: item.chart_id for item in definition.items}
        families: dict[UUID, dict[UUID, str]] = {}
        for dashboard_filter in definition.filters:
            allowed = (
                VALUE_FILTER_FAMILIES
                if dashboard_filter.kind is DashboardFilterKind.VALUES
                else DATE_RANGE_FILTER_FAMILIES
            )
            for target in dashboard_filter.targets:
                chart_id = charts[target.item_id]
                if chart_id not in families:
                    try:
                        families[chart_id] = await self._chart_queries.field_families(chart_id)
                    except (ChartNotFoundError, ChartDatasetNotAvailableError) as exc:
                        raise InvalidDashboardDefinitionError(
                            f"Filter {dashboard_filter.label!r} targets a chart without loaded data"
                        ) from exc
                if families[chart_id].get(target.field_id) not in allowed:
                    raise InvalidDashboardDefinitionError(
                        f"Filter {dashboard_filter.label!r} targets a field it cannot filter"
                    )
