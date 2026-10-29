"""Dashboard layout domain and application-service tests."""

import asyncio
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from datapipette.application.charts import (
    ChartContextFilter,
    ChartDatasetNotAvailableError,
    ChartFieldValues,
)
from datapipette.application.dashboards import (
    DashboardCreate,
    DashboardFilterNotFoundError,
    DashboardItemNotFoundError,
    DashboardNotFoundError,
    DashboardRevisionConflictError,
    DashboardService,
    InvalidDashboardCursorError,
    InvalidDashboardDefinitionError,
    InvalidDashboardFilterSelectionError,
)
from datapipette.domain.access_control import GrantPrincipalType
from datapipette.domain.charts import ChartQueryResult, ChartType
from datapipette.domain.dashboards import (
    Dashboard,
    DashboardDefinition,
    DashboardFilter,
    DashboardFilterKind,
    DashboardFilterSelection,
    DashboardFilterTarget,
    DashboardItem,
)
from datapipette.domain.identity import AccountStatus, SystemRole, User
from datapipette.domain.snapshot_queries import SnapshotRefreshState

NOW = datetime(2026, 10, 12, tzinfo=UTC)
CHART_ONE = uuid4()
CHART_TWO = uuid4()
DATASET_ID = uuid4()
SNAPSHOT_ID = uuid4()
REGION_FIELD = uuid4()
ORDERED_AT_FIELD = uuid4()
AMOUNT_FIELD = uuid4()


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


def _definition() -> DashboardDefinition:
    return DashboardDefinition(
        items=(
            DashboardItem(uuid4(), CHART_ONE, 0, 0, 6, 4),
            DashboardItem(uuid4(), CHART_TWO, 6, 0, 6, 4),
        )
    )


class FakeDashboardStore:
    def __init__(self, chart_owners: dict[UUID, UUID]) -> None:
        self.chart_owners = chart_owners
        self.dashboard: Dashboard | None = None
        self.audits: list[dict[str, Any]] = []
        self.grants: dict[UUID, Any] = {}
        self.shared_with: set[UUID] = set()

    async def create(self, **values: Any) -> Dashboard:
        value = values["value"]
        self.dashboard = Dashboard(
            id=values["dashboard_id"],
            owner_id=values["actor_id"],
            name=value.name,
            revision=1,
            definition=value.definition,
            created_at=values["occurred_at"],
            updated_at=values["occurred_at"],
        )
        return self.dashboard

    async def list(self, **values: Any) -> tuple[Dashboard, ...]:
        if self.dashboard is None:
            return ()
        actor_id = values["actor_id"]
        return (
            (self.dashboard,)
            if values["include_all"]
            or actor_id == self.dashboard.owner_id
            or actor_id in self.shared_with
            else ()
        )

    async def get(self, dashboard_id: UUID) -> Dashboard | None:
        if self.dashboard is None or self.dashboard.id != dashboard_id:
            return None
        return self.dashboard

    async def has_view_access(self, **values: Any) -> bool:
        return values["actor_id"] in self.shared_with

    async def list_grants(self, dashboard_id: UUID) -> tuple[Any, ...] | None:
        return tuple(self.grants.values()) if self.dashboard is not None else None

    async def create_grant(self, **values: Any) -> Any:
        from datapipette.domain.dashboards import DashboardGrant

        grant = DashboardGrant(
            values["grant_id"],
            values["dashboard_id"],
            values["principal_type"],
            values["principal_id"],
            values["actor_id"],
            values["occurred_at"],
        )
        self.grants[grant.id] = grant
        self.shared_with.add(grant.principal_id)
        return grant

    async def revoke_grant(self, **values: Any) -> bool | None:
        grant = self.grants.pop(values["grant_id"], None)
        if grant is None:
            return False
        self.shared_with.discard(grant.principal_id)
        return True

    async def resolve_chart_owners(self, chart_ids: frozenset[UUID]) -> dict[UUID, UUID]:
        return {
            chart_id: self.chart_owners[chart_id]
            for chart_id in chart_ids
            if chart_id in self.chart_owners
        }

    async def update(self, **values: Any) -> Dashboard | None:
        if self.dashboard is None or self.dashboard.id != values["dashboard_id"]:
            return None
        if self.dashboard.revision != values["expected_revision"]:
            raise DashboardRevisionConflictError
        definition = DashboardDefinition.from_document(values["definition"])
        if self.dashboard.name != values["name"] or self.dashboard.definition != definition:
            self.dashboard = replace(
                self.dashboard,
                name=values["name"],
                revision=self.dashboard.revision + 1,
                definition=definition,
                updated_at=values["occurred_at"],
            )
        return self.dashboard


class FakeChartQueries:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.families: dict[UUID, dict[UUID, str]] = {
            CHART_ONE: {REGION_FIELD: "text", ORDERED_AT_FIELD: "datetime", AMOUNT_FIELD: "number"},
            CHART_TWO: {REGION_FIELD: "text"},
        }
        self.values: dict[UUID, ChartFieldValues] = {
            CHART_ONE: ChartFieldValues(("West", "East"), truncated=False),
            CHART_TWO: ChartFieldValues(("North", "East"), truncated=True),
        }
        self.value_calls: list[dict[str, Any]] = []

    async def field_families(self, chart_id: UUID) -> dict[UUID, str]:
        if chart_id not in self.families:
            raise ChartDatasetNotAvailableError
        return self.families[chart_id]

    async def field_values(self, **values: Any) -> ChartFieldValues:
        self.value_calls.append(values)
        return self.values[values["chart_id"]]

    async def query(self, **values: Any) -> ChartQueryResult:
        return await self._result(values)

    async def query_dashboard_item(self, **values: Any) -> ChartQueryResult:
        return await self._result(values)

    async def _result(self, values: dict[str, Any]) -> ChartQueryResult:
        self.calls.append(values)
        return ChartQueryResult(
            policy_revision=0,
            chart_id=values["chart_id"],
            chart_name="Accounts",
            chart_type=ChartType.KPI,
            chart_revision=1,
            dataset_id=DATASET_ID,
            snapshot_id=SNAPSHOT_ID,
            snapshot_activated_at=NOW,
            refresh_state=SnapshotRefreshState.IDLE,
            last_refresh_error=None,
            last_refresh_error_at=None,
            is_stale=False,
            columns=(),
            rows=({"value": 1},),
            truncated_by_bytes=False,
        )


def test_dashboard_definition_round_trips_and_validates_grid() -> None:
    value = _definition()
    assert DashboardDefinition.from_document(value.to_document()) == value
    assert DashboardDefinition.from_document(DashboardDefinition().to_document()).items == ()
    with pytest.raises(ValueError, match="Stored dashboard definition is invalid"):
        DashboardDefinition.from_document({"schema_version": True, "items": []})

    first = value.items[0]
    with pytest.raises(ValueError, match="identifiers must be unique"):
        DashboardDefinition(items=(first, replace(value.items[1], id=first.id)))
    with pytest.raises(ValueError, match="chart references must be unique"):
        DashboardDefinition(items=(first, replace(value.items[1], chart_id=first.chart_id)))
    with pytest.raises(ValueError, match="must not overlap"):
        DashboardDefinition(items=(first, replace(value.items[1], x=5)))
    with pytest.raises(ValueError, match="beyond the grid"):
        DashboardItem(uuid4(), uuid4(), 10, 0, 3, 4)
    with pytest.raises(ValueError, match="geometry must use integers"):
        DashboardItem(uuid4(), uuid4(), True, 0, 3, 4)


def test_dashboard_service_crud_and_reference_ownership() -> None:
    actor = _user()
    store = FakeDashboardStore({CHART_ONE: actor.id, CHART_TWO: actor.id})
    chart_queries = FakeChartQueries()
    service = DashboardService(store, chart_queries)

    async def exercise() -> None:
        dashboard = await service.create(
            actor=actor,
            value=DashboardCreate(" Sales overview ", _definition()),
            request_id="req-create",
        )
        assert dashboard.name == "Sales overview"
        assert (await service.list(actor=actor, cursor=None, limit=10)).dashboards == (dashboard,)
        assert await service.get(actor=actor, dashboard_id=dashboard.id) == dashboard

        item_result = await service.query_item(
            actor=actor,
            dashboard_id=dashboard.id,
            item_id=dashboard.definition.items[0].id,
            request_id="req-query",
        )
        assert item_result.chart_result.chart_id == CHART_ONE
        assert item_result.dashboard_revision == 1
        assert chart_queries.calls[-1]["chart_id"] == CHART_ONE
        assert "dashboard_owner_id" not in chart_queries.calls[-1]
        with pytest.raises(DashboardItemNotFoundError):
            await service.query_item(
                actor=actor,
                dashboard_id=dashboard.id,
                item_id=uuid4(),
                request_id="req-missing-item",
            )

        page = await service.list(actor=actor, cursor=None, limit=1)
        assert page.next_cursor is None

        moved = replace(
            dashboard.definition,
            items=(dashboard.definition.items[0], replace(dashboard.definition.items[1], y=4)),
        )
        updated = await service.update(
            actor=actor,
            dashboard_id=dashboard.id,
            expected_revision=1,
            name="Sales dashboard",
            definition=moved,
            request_id="req-update",
        )
        assert updated.revision == 2

        admin = _user(SystemRole.ADMIN)
        assert await service.get(actor=admin, dashboard_id=dashboard.id) == updated
        admin_updated = await service.update(
            actor=admin,
            dashboard_id=dashboard.id,
            expected_revision=2,
            name="Admin-adjusted dashboard",
            definition=moved,
            request_id="req-admin-update",
        )
        assert admin_updated.owner_id == actor.id
        assert admin_updated.revision == 3

        store.chart_owners[CHART_TWO] = uuid4()
        with pytest.raises(InvalidDashboardDefinitionError, match="unavailable"):
            await service.update(
                actor=actor,
                dashboard_id=dashboard.id,
                expected_revision=3,
                name="Invalid",
                definition=_definition(),
                request_id="req-invalid",
            )

    asyncio.run(exercise())


def test_dashboard_service_conceals_resources_and_rejects_bad_cursor() -> None:
    actor = _user()
    other = _user()
    store = FakeDashboardStore({CHART_ONE: actor.id, CHART_TWO: actor.id})
    chart_queries = FakeChartQueries()
    service = DashboardService(store, chart_queries)

    async def exercise() -> None:
        dashboard = await service.create(
            actor=actor,
            value=DashboardCreate("Overview", _definition()),
            request_id="req-create",
        )
        with pytest.raises(InvalidDashboardCursorError):
            await service.list(actor=actor, cursor="bad!", limit=10)
        with pytest.raises(DashboardNotFoundError):
            await service.get(actor=other, dashboard_id=dashboard.id)
        viewer = replace(other, role=SystemRole.VIEWER)
        assert (await service.list(actor=viewer, cursor=None, limit=10)).dashboards == ()
        grant = await service.create_grant(
            actor=actor,
            dashboard_id=dashboard.id,
            principal_type=GrantPrincipalType.USER,
            principal_id=viewer.id,
            request_id="req-grant",
        )
        assert await service.get(actor=viewer, dashboard_id=dashboard.id) == dashboard
        assert (await service.list(actor=viewer, cursor=None, limit=10)).dashboards == (dashboard,)
        await service.query_item(
            actor=viewer,
            dashboard_id=dashboard.id,
            item_id=dashboard.definition.items[0].id,
            request_id="req-shared-query",
        )
        assert chart_queries.calls[-1]["dashboard_owner_id"] == actor.id
        with pytest.raises(DashboardNotFoundError):
            await service.update(
                actor=viewer,
                dashboard_id=dashboard.id,
                expected_revision=1,
                name="Forbidden",
                definition=dashboard.definition,
                request_id="req-forbidden",
            )
        await service.revoke_grant(
            actor=actor,
            dashboard_id=dashboard.id,
            grant_id=grant.id,
            request_id="req-revoke",
        )
        with pytest.raises(DashboardNotFoundError):
            await service.get(actor=viewer, dashboard_id=dashboard.id)
        with pytest.raises(DashboardNotFoundError):
            await service.create(
                actor=viewer,
                value=DashboardCreate("Viewer", DashboardDefinition()),
                request_id="req-viewer",
            )

    asyncio.run(exercise())


def _filtered_definition(
    definition: DashboardDefinition,
    *filters: DashboardFilter,
) -> DashboardDefinition:
    return replace(definition, schema_version=2, filters=filters)


def _region_filter(definition: DashboardDefinition) -> DashboardFilter:
    return DashboardFilter(
        uuid4(),
        "  Region ",
        DashboardFilterKind.VALUES,
        tuple(DashboardFilterTarget(item.id, REGION_FIELD) for item in definition.items),
    )


def test_dashboard_filters_round_trip_and_validate_targets() -> None:
    layout = _definition()
    region = _region_filter(layout)
    value = _filtered_definition(layout, region)
    assert value.filters[0].label == "Region"
    assert DashboardDefinition.from_document(value.to_document()) == value
    assert "filters" not in layout.to_document()
    assert DashboardDefinition.from_document(layout.to_document()).filters == ()
    assert value.filter(region.id) == region

    with pytest.raises(ValueError, match="require definition schema version 2"):
        replace(layout, filters=(region,))
    with pytest.raises(ValueError, match="target items in the layout"):
        _filtered_definition(
            layout,
            replace(region, targets=(DashboardFilterTarget(uuid4(), REGION_FIELD),)),
        )
    with pytest.raises(ValueError, match="each item only once"):
        DashboardFilter(
            uuid4(),
            "Region",
            DashboardFilterKind.VALUES,
            (
                DashboardFilterTarget(layout.items[0].id, REGION_FIELD),
                DashboardFilterTarget(layout.items[0].id, AMOUNT_FIELD),
            ),
        )
    with pytest.raises(ValueError, match="at least one item"):
        DashboardFilter(uuid4(), "Region", DashboardFilterKind.VALUES, ())
    with pytest.raises(ValueError, match="labels need"):
        replace(region, label="   ")
    with pytest.raises(ValueError, match="at most 5 filters"):
        _filtered_definition(layout, *(replace(region, id=uuid4()) for _ in range(6)))
    with pytest.raises(ValueError, match="identifiers must be unique"):
        _filtered_definition(layout, region, region)
    with pytest.raises(ValueError, match="start date must not be after"):
        DashboardFilterSelection(uuid4(), date_from=date(2026, 2, 1), date_to=date(2026, 1, 1))


def test_dashboard_service_validates_filter_fields_and_scopes_selections() -> None:
    actor = _user()
    store = FakeDashboardStore({CHART_ONE: actor.id, CHART_TWO: actor.id})
    chart_queries = FakeChartQueries()
    service = DashboardService(store, chart_queries)
    layout = _definition()
    region = _region_filter(layout)
    ordered = DashboardFilter(
        uuid4(),
        "Ordered",
        DashboardFilterKind.DATE_RANGE,
        (DashboardFilterTarget(layout.items[0].id, ORDERED_AT_FIELD),),
    )

    async def exercise() -> None:
        with pytest.raises(InvalidDashboardDefinitionError, match="cannot filter"):
            await service.create(
                actor=actor,
                value=DashboardCreate(
                    "Wrong field",
                    _filtered_definition(
                        layout,
                        replace(
                            region,
                            targets=(DashboardFilterTarget(layout.items[0].id, AMOUNT_FIELD),),
                        ),
                    ),
                ),
                request_id="req-wrong-family",
            )
        chart_queries.families.pop(CHART_TWO)
        with pytest.raises(InvalidDashboardDefinitionError, match="without loaded data"):
            await service.create(
                actor=actor,
                value=DashboardCreate("Unloaded", _filtered_definition(layout, region)),
                request_id="req-unloaded",
            )
        chart_queries.families[CHART_TWO] = {REGION_FIELD: "text"}
        dashboard = await service.create(
            actor=actor,
            value=DashboardCreate("Filtered", _filtered_definition(layout, region, ordered)),
            request_id="req-create",
        )

        selections = (
            DashboardFilterSelection(region.id, values=("East",)),
            DashboardFilterSelection(ordered.id, date_from=date(2026, 1, 1)),
        )
        await service.query_item(
            actor=actor,
            dashboard_id=dashboard.id,
            item_id=layout.items[0].id,
            request_id="req-first",
            selections=selections,
        )
        assert chart_queries.calls[-1]["context_filters"] == (
            ChartContextFilter(REGION_FIELD, values=("East",)),
            ChartContextFilter(ORDERED_AT_FIELD, date_from=date(2026, 1, 1)),
        )
        await service.query_item(
            actor=actor,
            dashboard_id=dashboard.id,
            item_id=layout.items[1].id,
            request_id="req-second",
            selections=selections,
        )
        assert chart_queries.calls[-1]["context_filters"] == (
            ChartContextFilter(REGION_FIELD, values=("East",)),
        )
        for bad, message in (
            ((DashboardFilterSelection(uuid4(), values=("East",)),), "no such filter"),
            ((DashboardFilterSelection(region.id, date_from=date(2026, 1, 1)),), "expects values"),
            ((DashboardFilterSelection(ordered.id, values=("East",)),), "expects a date range"),
            ((selections[0], selections[0]), "selected once"),
        ):
            with pytest.raises(InvalidDashboardFilterSelectionError, match=message):
                await service.query_item(
                    actor=actor,
                    dashboard_id=dashboard.id,
                    item_id=layout.items[0].id,
                    request_id="req-bad",
                    selections=bad,
                )
        await service.query_item(
            actor=actor,
            dashboard_id=dashboard.id,
            item_id=layout.items[0].id,
            request_id="req-empty",
            selections=(DashboardFilterSelection(region.id),),
        )
        assert chart_queries.calls[-1]["context_filters"] == ()

        values = await service.list_filter_values(
            actor=actor, dashboard_id=dashboard.id, filter_id=region.id
        )
        assert values.values == ("East", "North", "West")
        assert values.truncated is True
        assert chart_queries.value_calls[-1]["direct_access"] is True
        with pytest.raises(DashboardFilterNotFoundError):
            await service.list_filter_values(
                actor=actor, dashboard_id=dashboard.id, filter_id=ordered.id
            )

        viewer = replace(_user(SystemRole.VIEWER), id=uuid4())
        await service.create_grant(
            actor=actor,
            dashboard_id=dashboard.id,
            principal_type=GrantPrincipalType.USER,
            principal_id=viewer.id,
            request_id="req-grant",
        )
        await service.list_filter_values(
            actor=viewer, dashboard_id=dashboard.id, filter_id=region.id
        )
        assert chart_queries.value_calls[-1]["direct_access"] is False
        assert chart_queries.value_calls[-1]["dashboard_owner_id"] == actor.id

    asyncio.run(exercise())
