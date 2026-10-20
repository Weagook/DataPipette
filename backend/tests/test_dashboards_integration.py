"""Private dashboard layout API integration tests."""

import asyncio
import os
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy import select, text

from datapipette.api.dependencies.authentication import CSRF_COOKIE_NAME
from datapipette.application.auth_service import bootstrap_first_admin
from datapipette.application.charts import (
    ChartDatasetNotAvailableError,
    ChartQueryCapacityExceededError,
    ChartQueryResultTooLargeError,
    ChartQueryTimeoutError,
    ChartQueryUnavailableError,
    ChartService,
    InvalidChartDefinitionError,
)
from datapipette.config import Settings
from datapipette.domain.charts import (
    ChartQueryResult,
    ChartResultColumn,
    ChartResultColumnRole,
    ChartType,
)
from datapipette.domain.snapshot_queries import SnapshotRefreshState
from datapipette.infrastructure.database import create_database_engine, create_session_factory
from datapipette.infrastructure.models import (
    AuditEventModel,
    ChartModel,
    DashboardChartDependencyModel,
    DatasetModel,
    PostgresConnectionModel,
    UserModel,
)
from datapipette.infrastructure.repositories.authentication import SqlAlchemyAuthenticationStore
from datapipette.main import create_app

ADMIN_EMAIL = "dashboard-admin@example.com"
ADMIN_PASSWORD = "correct horse battery staple"
VIEWER_EMAIL = "dashboard-viewer@example.com"
VIEWER_PASSWORD = "viewer password for dashboard tests"


class StubChartQueryService:
    def __init__(
        self,
        available_chart_id: UUID,
        unavailable_chart_id: UUID,
        available_dataset_id: UUID,
    ) -> None:
        self.available_chart_id = available_chart_id
        self.unavailable_chart_id = unavailable_chart_id
        self.available_dataset_id = available_dataset_id
        self.calls: list[UUID] = []
        self.unavailable_error: Exception = ChartDatasetNotAvailableError()

    async def query(self, **values: Any) -> ChartQueryResult:
        return await self._result(values)

    async def query_dashboard_item(self, **values: Any) -> ChartQueryResult:
        return await self._result(values)

    async def _result(self, values: dict[str, Any]) -> ChartQueryResult:
        chart_id = cast(UUID, values["chart_id"])
        self.calls.append(chart_id)
        if chart_id == self.unavailable_chart_id:
            raise self.unavailable_error
        assert chart_id == self.available_chart_id
        return ChartQueryResult(
            policy_revision=0,
            chart_id=chart_id,
            chart_name="Accounts",
            chart_type=ChartType.KPI,
            chart_revision=3,
            dataset_id=self.available_dataset_id,
            snapshot_id=uuid4(),
            snapshot_activated_at=datetime.now(UTC),
            refresh_state=SnapshotRefreshState.IDLE,
            last_refresh_error=None,
            last_refresh_error_at=None,
            is_stale=False,
            columns=(
                ChartResultColumn(
                    key="count",
                    role=ChartResultColumnRole.MEASURE,
                    field_id=uuid4(),
                    measure_id=uuid4(),
                    label="Count",
                    data_type="bigint",
                ),
            ),
            rows=({"count": 42},),
            truncated_by_bytes=False,
        )


@pytest.fixture
def dashboard_settings(tmp_path: Path) -> Settings:
    database_url = os.environ.get(
        "DATAPIPETTE_TEST_DATABASE_URL",
        "postgresql+psycopg://datapipette:datapipette_dev_only@localhost:5432/datapipette",
    )
    return Settings(
        environment="test",
        docs_enabled=False,
        snapshot_storage_path=tmp_path / "snapshots",
        metadata_database_url=SecretStr(database_url),
    )


async def _reset(settings: Settings) -> None:
    engine = create_database_engine(settings)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "TRUNCATE dashboard_acl_grants, dashboard_chart_dependencies, dashboards, "
                    "chart_field_dependencies, charts, dataset_snapshots, "
                    "dataset_refresh_requests, dataset_refresh_runs, dataset_versions, datasets, "
                    "postgresql_connections, group_memberships, identity_groups, "
                    "browser_sessions, audit_events, users "
                    "RESTART IDENTITY CASCADE"
                )
            )
    finally:
        await engine.dispose()
    redis = Redis.from_url(settings.redis_url.get_secret_value(), decode_responses=True)
    try:
        await redis.flushdb()
    finally:
        await redis.aclose()


@pytest.fixture(autouse=True)
def clean_state(dashboard_settings: Settings) -> Generator[None, None, None]:
    asyncio.run(_reset(dashboard_settings))
    yield
    asyncio.run(_reset(dashboard_settings))


async def _seed_charts(settings: Settings) -> tuple[UUID, UUID, UUID, UUID]:
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        await bootstrap_first_admin(
            SqlAlchemyAuthenticationStore(session_factory),
            ADMIN_EMAIL,
            "Dashboard Admin",
            ADMIN_PASSWORD,
        )
        now = datetime.now(UTC)
        connection_id = uuid4()
        dataset_one_id = uuid4()
        dataset_two_id = uuid4()
        chart_one_id = uuid4()
        chart_two_id = uuid4()
        async with session_factory() as session, session.begin():
            admin = await session.scalar(select(UserModel).where(UserModel.email == ADMIN_EMAIL))
            assert admin is not None
            session.add(
                PostgresConnectionModel(
                    id=connection_id,
                    name="Dashboard source",
                    host="source.invalid",
                    port=5432,
                    database_name="analytics",
                    username="readonly",
                    encrypted_password="encrypted",
                    encrypted_ca_certificate=None,
                    ssl_mode="disable",
                    created_by=admin.id,
                    last_tested_at=None,
                    last_test_succeeded=None,
                    last_test_error_code=None,
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.flush()
            session.add_all(
                (
                    DatasetModel(
                        id=dataset_one_id,
                        name="Sales",
                        owner_id=admin.id,
                        connection_id=connection_id,
                        active_version_id=None,
                        active_snapshot_id=None,
                        created_at=now,
                        updated_at=now,
                    ),
                    DatasetModel(
                        id=dataset_two_id,
                        name="Operations",
                        owner_id=admin.id,
                        connection_id=connection_id,
                        active_version_id=None,
                        active_snapshot_id=None,
                        created_at=now,
                        updated_at=now,
                    ),
                )
            )
            await session.flush()
            definition = {
                "schema_version": 2,
                "chart_type": "kpi",
                "dimensions": [],
                "measures": [
                    {
                        "id": str(uuid4()),
                        "field_id": str(uuid4()),
                        "aggregation": "count",
                        "label": "Count",
                    }
                ],
                "filters": [],
                "sorts": [],
                "limit": 1,
            }
            session.add_all(
                (
                    ChartModel(
                        id=chart_one_id,
                        dataset_id=dataset_one_id,
                        owner_id=admin.id,
                        name="Sales KPI",
                        revision=1,
                        definition=definition,
                        created_at=now,
                        updated_at=now,
                    ),
                    ChartModel(
                        id=chart_two_id,
                        dataset_id=dataset_two_id,
                        owner_id=admin.id,
                        name="Operations KPI",
                        revision=1,
                        definition=definition,
                        created_at=now,
                        updated_at=now,
                    ),
                )
            )
        return chart_one_id, chart_two_id, dataset_one_id, dataset_two_id
    finally:
        await engine.dispose()


def _login(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/sessions",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
    )
    assert response.status_code == 201, response.text


def _csrf(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies[CSRF_COOKIE_NAME]}


def test_dashboard_api_persists_multi_dataset_layout_and_dependencies(
    dashboard_settings: Settings,
) -> None:
    chart_one_id, chart_two_id, dataset_one_id, dataset_two_id = asyncio.run(
        _seed_charts(dashboard_settings)
    )
    item_one_id = uuid4()
    item_two_id = uuid4()
    definition: dict[str, Any] = {
        "schema_version": 1,
        "items": [
            {
                "id": str(item_one_id),
                "chart_id": str(chart_one_id),
                "x": 0,
                "y": 0,
                "width": 6,
                "height": 4,
            },
            {
                "id": str(item_two_id),
                "chart_id": str(chart_two_id),
                "x": 6,
                "y": 0,
                "width": 6,
                "height": 4,
            },
        ],
    }
    payload = {"name": "Executive overview", "definition": definition}
    chart_queries = StubChartQueryService(chart_one_id, chart_two_id, dataset_one_id)

    with TestClient(
        create_app(
            dashboard_settings,
            chart_service=cast(ChartService, chart_queries),
        )
    ) as client:
        _login(client)
        created = client.post("/api/v1/dashboards", json=payload, headers=_csrf(client))
        assert created.status_code == 201, created.text
        dashboard_id = cast(str, created.json()["id"])
        assert created.headers["etag"] == '"dashboard-1"'

        viewer_created = client.post(
            "/api/v1/users",
            json={
                "email": VIEWER_EMAIL,
                "display_name": "Dashboard Viewer",
                "password": VIEWER_PASSWORD,
                "role": "viewer",
            },
            headers=_csrf(client),
        )
        assert viewer_created.status_code == 201, viewer_created.text
        viewer_id = viewer_created.json()["id"]
        direct_grant = client.post(
            f"/api/v1/dashboards/{dashboard_id}/grants",
            json={"principal_type": "user", "principal_id": viewer_id},
            headers=_csrf(client),
        )
        assert direct_grant.status_code == 201, direct_grant.text
        grant_id = direct_grant.json()["id"]
        duplicate_grant = client.post(
            f"/api/v1/dashboards/{dashboard_id}/grants",
            json={"principal_type": "user", "principal_id": viewer_id},
            headers=_csrf(client),
        )
        assert duplicate_grant.status_code == 201
        assert duplicate_grant.json()["id"] == grant_id
        missing_principal = client.post(
            f"/api/v1/dashboards/{dashboard_id}/grants",
            json={"principal_type": "user", "principal_id": str(uuid4())},
            headers=_csrf(client),
        )
        assert missing_principal.status_code == 404
        assert missing_principal.json()["code"] == "dashboard_grant_principal_not_found"
        missing_grant = client.delete(
            f"/api/v1/dashboards/{dashboard_id}/grants/{uuid4()}", headers=_csrf(client)
        )
        assert missing_grant.status_code == 404
        assert missing_grant.json()["code"] == "dashboard_grant_not_found"
        assert client.get(f"/api/v1/dashboards/{dashboard_id}/grants").json()["data"] == [
            direct_grant.json()
        ]

        with TestClient(
            create_app(
                dashboard_settings,
                chart_service=cast(ChartService, chart_queries),
            )
        ) as viewer_client:
            login = viewer_client.post(
                "/api/v1/auth/sessions",
                json={"email": VIEWER_EMAIL, "password": VIEWER_PASSWORD},
            )
            assert login.status_code == 201, login.text
            shared_list = viewer_client.get("/api/v1/dashboards")
            assert [item["id"] for item in shared_list.json()["data"]] == [dashboard_id]
            assert viewer_client.get(f"/api/v1/dashboards/{dashboard_id}").status_code == 200
            assert viewer_client.get(f"/api/v1/dashboards/{dashboard_id}/grants").status_code == 404
            shared_item = viewer_client.post(
                f"/api/v1/dashboards/{dashboard_id}/items/{item_one_id}/data:query"
            )
            assert shared_item.status_code == 200, shared_item.text
            forbidden_edit = viewer_client.put(
                f"/api/v1/dashboards/{dashboard_id}",
                json=payload,
                headers={**_csrf(viewer_client), "If-Match": created.headers["etag"]},
            )
            assert forbidden_edit.status_code == 404

            revoked = client.delete(
                f"/api/v1/dashboards/{dashboard_id}/grants/{grant_id}",
                headers=_csrf(client),
            )
            assert revoked.status_code == 204
            assert viewer_client.get(f"/api/v1/dashboards/{dashboard_id}").status_code == 404

            group = client.post(
                "/api/v1/groups",
                json={"name": "Leadership"},
                headers=_csrf(client),
            )
            assert group.status_code == 201, group.text
            group_id = group.json()["id"]
            assert (
                client.put(
                    f"/api/v1/groups/{group_id}/members/{viewer_id}", headers=_csrf(client)
                ).status_code
                == 200
            )
            group_grant = client.post(
                f"/api/v1/dashboards/{dashboard_id}/grants",
                json={"principal_type": "group", "principal_id": group_id},
                headers=_csrf(client),
            )
            assert group_grant.status_code == 201, group_grant.text
            assert viewer_client.get(f"/api/v1/dashboards/{dashboard_id}").status_code == 200
            assert (
                client.delete(
                    f"/api/v1/groups/{group_id}/members/{viewer_id}", headers=_csrf(client)
                ).status_code
                == 204
            )
            assert viewer_client.get(f"/api/v1/dashboards/{dashboard_id}").status_code == 404

        read = client.get(f"/api/v1/dashboards/{dashboard_id}")
        assert read.status_code == 200
        assert read.json()["definition"] == definition

        successful_item = client.post(
            f"/api/v1/dashboards/{dashboard_id}/items/{item_one_id}/data:query"
        )
        assert successful_item.status_code == 200, successful_item.text
        assert successful_item.json()["data"]["rows"] == [{"count": 42}]
        assert successful_item.json()["meta"]["dashboard_id"] == dashboard_id
        assert successful_item.json()["meta"]["dashboard_revision"] == 1
        assert successful_item.json()["meta"]["item_id"] == str(item_one_id)
        assert successful_item.json()["meta"]["chart_id"] == str(chart_one_id)
        assert successful_item.json()["meta"]["chart_name"] == "Accounts"
        assert successful_item.json()["meta"]["chart_type"] == "kpi"
        assert successful_item.json()["meta"]["dataset_id"] == str(dataset_one_id)
        assert successful_item.json()["meta"]["policy_revision"] == 0

        failed_item = client.post(
            f"/api/v1/dashboards/{dashboard_id}/items/{item_two_id}/data:query"
        )
        assert failed_item.status_code == 404
        assert failed_item.json()["code"] == "dashboard_item_unavailable"

        failure_cases = (
            (InvalidChartDefinitionError(), 409, "dashboard_item_invalid"),
            (
                ChartQueryResultTooLargeError(),
                413,
                "dashboard_item_result_row_too_large",
            ),
            (ChartQueryUnavailableError(), 503, "dashboard_item_query_unavailable"),
            (
                ChartQueryCapacityExceededError(),
                503,
                "interactive_query_capacity_exceeded",
            ),
            (ChartQueryTimeoutError(), 504, "dashboard_item_query_timeout"),
        )
        for error, expected_status, expected_code in failure_cases:
            chart_queries.unavailable_error = error
            item_failure = client.post(
                f"/api/v1/dashboards/{dashboard_id}/items/{item_two_id}/data:query"
            )
            assert item_failure.status_code == expected_status
            assert item_failure.json()["code"] == expected_code
            if expected_code == "interactive_query_capacity_exceeded":
                assert item_failure.headers["retry-after"] == "1"

        successful_retry = client.post(
            f"/api/v1/dashboards/{dashboard_id}/items/{item_one_id}/data:query"
        )
        assert successful_retry.status_code == 200
        assert chart_queries.calls[0] == chart_one_id
        assert chart_queries.calls[-1] == chart_one_id
        assert chart_queries.calls.count(chart_two_id) == 6

        missing_item = client.post(f"/api/v1/dashboards/{dashboard_id}/items/{uuid4()}/data:query")
        assert missing_item.status_code == 404
        assert missing_item.json()["code"] == "dashboard_item_not_found"

        duplicate = client.post("/api/v1/dashboards", json=payload, headers=_csrf(client))
        assert duplicate.status_code == 409
        assert duplicate.json()["code"] == "dashboard_name_conflict"

        unavailable = client.post(
            "/api/v1/dashboards",
            json={
                "name": "Unavailable chart",
                "definition": {
                    "schema_version": 1,
                    "items": [
                        {
                            "id": str(uuid4()),
                            "chart_id": str(uuid4()),
                            "x": 0,
                            "y": 0,
                            "width": 6,
                            "height": 4,
                        }
                    ],
                },
            },
            headers=_csrf(client),
        )
        assert unavailable.status_code == 422
        assert unavailable.json()["code"] == "dashboard_definition_invalid"

        overlapping = client.post(
            "/api/v1/dashboards",
            json={
                "name": "Overlapping layout",
                "definition": {
                    "schema_version": 1,
                    "items": [
                        definition["items"][0],
                        {**definition["items"][1], "x": 5},
                    ],
                },
            },
            headers=_csrf(client),
        )
        assert overlapping.status_code == 422
        assert overlapping.json()["code"] == "dashboard_definition_invalid"

        boolean_geometry = client.post(
            "/api/v1/dashboards",
            json={
                "name": "Boolean geometry",
                "definition": {
                    "schema_version": 1,
                    "items": [{**definition["items"][0], "x": True}],
                },
            },
            headers=_csrf(client),
        )
        assert boolean_geometry.status_code == 422

        missing_precondition = client.put(
            f"/api/v1/dashboards/{dashboard_id}",
            json=payload,
            headers=_csrf(client),
        )
        assert missing_precondition.status_code == 428
        assert missing_precondition.json()["code"] == "dashboard_revision_required"

        malformed_precondition = client.put(
            f"/api/v1/dashboards/{dashboard_id}",
            json=payload,
            headers={**_csrf(client), "If-Match": '"chart-1"'},
        )
        assert malformed_precondition.status_code == 400
        assert malformed_precondition.json()["code"] == "invalid_dashboard_revision"

        no_op = client.put(
            f"/api/v1/dashboards/{dashboard_id}",
            json=payload,
            headers={**_csrf(client), "If-Match": created.headers["etag"]},
        )
        assert no_op.status_code == 200
        assert no_op.headers["etag"] == '"dashboard-1"'

        moved_definition = {
            **definition,
            "items": [definition["items"][0], {**definition["items"][1], "y": 4}],
        }
        updated = client.put(
            f"/api/v1/dashboards/{dashboard_id}",
            json={"name": "Executive dashboard", "definition": moved_definition},
            headers={**_csrf(client), "If-Match": created.headers["etag"]},
        )
        assert updated.status_code == 200, updated.text
        assert updated.headers["etag"] == '"dashboard-2"'

        stale = client.put(
            f"/api/v1/dashboards/{dashboard_id}",
            json=payload,
            headers={**_csrf(client), "If-Match": created.headers["etag"]},
        )
        assert stale.status_code == 412

        listing = client.get("/api/v1/dashboards")
        assert listing.status_code == 200
        assert [item["id"] for item in listing.json()["data"]] == [dashboard_id]

        invalid_cursor = client.get("/api/v1/dashboards", params={"cursor": "bad!"})
        assert invalid_cursor.status_code == 400

    async def verify_metadata() -> None:
        engine = create_database_engine(dashboard_settings)
        try:
            async with create_session_factory(engine)() as session:
                dependencies = list(
                    await session.scalars(
                        select(DashboardChartDependencyModel).where(
                            DashboardChartDependencyModel.dashboard_id == UUID(dashboard_id)
                        )
                    )
                )
                dataset_ids = set(
                    await session.scalars(
                        select(ChartModel.dataset_id).where(
                            ChartModel.id.in_([item.chart_id for item in dependencies])
                        )
                    )
                )
                actions = set(
                    await session.scalars(
                        select(AuditEventModel.action).where(
                            AuditEventModel.resource_id == UUID(dashboard_id)
                        )
                    )
                )
            assert {item.chart_id for item in dependencies} == {chart_one_id, chart_two_id}
            assert dataset_ids == {dataset_one_id, dataset_two_id}
            assert actions == {
                "dashboard.created",
                "dashboard.updated",
                "dashboard.grant_created",
                "dashboard.grant_revoked",
            }
        finally:
            await engine.dispose()

    asyncio.run(verify_metadata())
