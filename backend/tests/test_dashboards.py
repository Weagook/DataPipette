"""Dashboard layout domain and application-service tests."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from datepipette.application.dashboards import (
    DashboardCreate,
    DashboardItemNotFoundError,
    DashboardNotFoundError,
    DashboardRevisionConflictError,
    DashboardService,
    InvalidDashboardCursorError,
    InvalidDashboardDefinitionError,
)
from datepipette.domain.charts import ChartQueryResult
from datepipette.domain.dashboards import Dashboard, DashboardDefinition, DashboardItem
from datepipette.domain.identity import AccountStatus, SystemRole, User
from datepipette.domain.snapshot_queries import SnapshotRefreshState

NOW = datetime(2026, 10, 12, tzinfo=UTC)
CHART_ONE = uuid4()
CHART_TWO = uuid4()
DATASET_ID = uuid4()
SNAPSHOT_ID = uuid4()


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
        owner_id = values["owner_id"]
        return (self.dashboard,) if owner_id is None or owner_id == self.dashboard.owner_id else ()

    async def get(self, dashboard_id: UUID) -> Dashboard | None:
        if self.dashboard is None or self.dashboard.id != dashboard_id:
            return None
        return self.dashboard

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

    async def query(self, **values: Any) -> ChartQueryResult:
        self.calls.append(values)
        return ChartQueryResult(
            chart_id=values["chart_id"],
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
    service = DashboardService(store, FakeChartQueries())

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
        with pytest.raises(DashboardNotFoundError):
            await service.create(
                actor=viewer,
                value=DashboardCreate("Viewer", DashboardDefinition()),
                request_id="req-viewer",
            )

    asyncio.run(exercise())
