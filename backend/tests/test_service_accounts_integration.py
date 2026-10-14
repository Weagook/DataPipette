"""Service-account token lifecycle and scoped dashboard access integration."""

import asyncio
import os
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy import select, text, update

from datepipette.api.dependencies.authentication import CSRF_COOKIE_NAME
from datepipette.application.auth_service import bootstrap_first_admin
from datepipette.application.charts import ChartService
from datepipette.config import Settings
from datepipette.domain.charts import ChartQueryResult
from datepipette.domain.identity import AccountType
from datepipette.domain.snapshot_queries import SnapshotRefreshState
from datepipette.infrastructure.database import create_database_engine, create_session_factory
from datepipette.infrastructure.models import (
    ApiTokenModel,
    AuditEventModel,
    ChartModel,
    DatasetModel,
    PostgresConnectionModel,
    UserModel,
)
from datepipette.infrastructure.repositories.authentication import SqlAlchemyAuthenticationStore
from datepipette.main import create_app

ADMIN_EMAIL = "service-admin@example.com"
ADMIN_PASSWORD = "correct horse battery staple"


class StubChartService:
    def __init__(self, chart_id: UUID, dataset_id: UUID) -> None:
        self.chart_id = chart_id
        self.dataset_id = dataset_id
        self.actors: list[AccountType] = []

    async def query(self, **values: Any) -> ChartQueryResult:
        return await self._result(values)

    async def query_dashboard_item(self, **values: Any) -> ChartQueryResult:
        return await self._result(values)

    async def _result(self, values: dict[str, Any]) -> ChartQueryResult:
        self.actors.append(values["actor"].account_type)
        return ChartQueryResult(
            chart_id=self.chart_id,
            chart_revision=1,
            dataset_id=self.dataset_id,
            snapshot_id=uuid4(),
            snapshot_activated_at=datetime.now(UTC),
            refresh_state=SnapshotRefreshState.IDLE,
            last_refresh_error=None,
            last_refresh_error_at=None,
            is_stale=False,
            columns=(),
            rows=({"count": 7},),
            truncated_by_bytes=False,
        )


@pytest.fixture
def service_settings(tmp_path: Path) -> Settings:
    database_url = os.environ.get(
        "DATEPIPETTE_TEST_DATABASE_URL",
        "postgresql+psycopg://datepipette:datepipette_dev_only@localhost:5432/datepipette",
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
                    "TRUNCATE api_tokens, dashboard_acl_grants, dashboard_chart_dependencies, "
                    "dashboards, chart_field_dependencies, charts, dataset_snapshots, "
                    "dataset_refresh_requests, dataset_refresh_runs, dataset_versions, datasets, "
                    "postgresql_connections, group_memberships, identity_groups, "
                    "browser_sessions, audit_events, users RESTART IDENTITY CASCADE"
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
def clean_state(service_settings: Settings) -> Generator[None, None, None]:
    asyncio.run(_reset(service_settings))
    yield
    asyncio.run(_reset(service_settings))


async def _seed(settings: Settings) -> tuple[UUID, UUID]:
    engine = create_database_engine(settings)
    factory = create_session_factory(engine)
    try:
        await bootstrap_first_admin(
            SqlAlchemyAuthenticationStore(factory),
            ADMIN_EMAIL,
            "Service Admin",
            ADMIN_PASSWORD,
        )
        now = datetime.now(UTC)
        connection_id = uuid4()
        dataset_id = uuid4()
        chart_id = uuid4()
        async with factory() as session, session.begin():
            admin = await session.scalar(select(UserModel).where(UserModel.email == ADMIN_EMAIL))
            assert admin is not None
            session.add(
                PostgresConnectionModel(
                    id=connection_id,
                    name="Service source",
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
            session.add(
                DatasetModel(
                    id=dataset_id,
                    name="Service dataset",
                    owner_id=admin.id,
                    connection_id=connection_id,
                    active_version_id=None,
                    active_snapshot_id=None,
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.flush()
            session.add(
                ChartModel(
                    id=chart_id,
                    dataset_id=dataset_id,
                    owner_id=admin.id,
                    name="Service KPI",
                    revision=1,
                    definition={
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
                    },
                    created_at=now,
                    updated_at=now,
                )
            )
        return chart_id, dataset_id
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


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_service_tokens_are_one_time_scoped_revocable_and_rotatable(
    service_settings: Settings,
) -> None:
    chart_id, dataset_id = asyncio.run(_seed(service_settings))
    chart_service = StubChartService(chart_id, dataset_id)
    item_id = uuid4()

    with TestClient(
        create_app(service_settings, chart_service=cast(ChartService, chart_service))
    ) as admin:
        _login(admin)
        created_account = admin.post(
            "/api/v1/service-accounts",
            json={"name": "Embedded CRM", "role": "viewer"},
            headers=_csrf(admin),
        )
        assert created_account.status_code == 201, created_account.text
        account_id = created_account.json()["id"]

        assert admin.get("/api/v1/users").json()["data"]
        assert all(item["id"] != account_id for item in admin.get("/api/v1/users").json()["data"])
        duplicate = admin.post(
            "/api/v1/service-accounts",
            json={"name": "embedded crm", "role": "viewer"},
            headers=_csrf(admin),
        )
        assert duplicate.status_code == 409
        prohibited_admin = admin.post(
            "/api/v1/service-accounts",
            json={"name": "Too powerful", "role": "admin"},
            headers=_csrf(admin),
        )
        assert prohibited_admin.status_code == 422

        dashboard = admin.post(
            "/api/v1/dashboards",
            json={
                "name": "Embedded dashboard",
                "definition": {
                    "schema_version": 1,
                    "items": [
                        {
                            "id": str(item_id),
                            "chart_id": str(chart_id),
                            "x": 0,
                            "y": 0,
                            "width": 6,
                            "height": 4,
                        }
                    ],
                },
            },
            headers=_csrf(admin),
        )
        assert dashboard.status_code == 201, dashboard.text
        dashboard_id = dashboard.json()["id"]

        primary = admin.post(
            f"/api/v1/service-accounts/{account_id}/tokens",
            json={"name": "Primary", "scopes": ["dashboards:read"]},
            headers=_csrf(admin),
        )
        assert primary.status_code == 201, primary.text
        primary_secret = primary.json()["token"]
        primary_id = primary.json()["id"]
        assert primary_secret.startswith("dpp_")
        assert primary.json()["expires_at"] is None

        rotation = admin.post(
            f"/api/v1/service-accounts/{account_id}/tokens",
            json={
                "name": "Rotation",
                "scopes": ["dashboards:read"],
                "expires_at": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
            },
            headers=_csrf(admin),
        )
        assert rotation.status_code == 201, rotation.text
        rotation_secret = rotation.json()["token"]
        rotation_id = rotation.json()["id"]

        listed = admin.get(f"/api/v1/service-accounts/{account_id}/tokens")
        assert listed.status_code == 200
        assert {item["id"] for item in listed.json()["data"]} == {primary_id, rotation_id}
        assert all("token" not in item for item in listed.json()["data"])

        with TestClient(
            create_app(service_settings, chart_service=cast(ChartService, chart_service))
        ) as integration:
            assert (
                integration.get("/api/v1/dashboards", headers=_bearer(primary_secret)).json()[
                    "data"
                ]
                == []
            )
            grant = admin.post(
                f"/api/v1/dashboards/{dashboard_id}/grants",
                json={"principal_type": "user", "principal_id": account_id},
                headers=_csrf(admin),
            )
            assert grant.status_code == 201, grant.text

            visible = integration.get("/api/v1/dashboards", headers=_bearer(primary_secret))
            assert visible.status_code == 200, visible.text
            assert [item["id"] for item in visible.json()["data"]] == [dashboard_id]
            queried = integration.post(
                f"/api/v1/dashboards/{dashboard_id}/items/{item_id}/data:query",
                headers=_bearer(primary_secret),
            )
            assert queried.status_code == 200, queried.text
            assert queried.json()["data"]["rows"] == [{"count": 7}]
            assert chart_service.actors[-1] is AccountType.SERVICE

            closed_api = integration.get(
                "/api/v1/service-accounts", headers=_bearer(primary_secret)
            )
            assert closed_api.status_code == 401

            revoked = admin.delete(
                f"/api/v1/service-accounts/{account_id}/tokens/{primary_id}",
                headers=_csrf(admin),
            )
            assert revoked.status_code == 204
            assert (
                integration.get("/api/v1/dashboards", headers=_bearer(primary_secret)).status_code
                == 401
            )
            assert (
                integration.get("/api/v1/dashboards", headers=_bearer(rotation_secret)).status_code
                == 200
            )

            disabled = admin.patch(
                f"/api/v1/service-accounts/{account_id}",
                json={"status": "disabled"},
                headers=_csrf(admin),
            )
            assert disabled.status_code == 200
            assert (
                integration.get("/api/v1/dashboards", headers=_bearer(rotation_secret)).status_code
                == 401
            )

        async def verify_storage() -> None:
            engine = create_database_engine(service_settings)
            try:
                async with create_session_factory(engine)() as session:
                    tokens = list(
                        await session.scalars(
                            select(ApiTokenModel).where(
                                ApiTokenModel.service_account_id == UUID(account_id)
                            )
                        )
                    )
                    events = list(
                        await session.scalars(
                            select(AuditEventModel).where(
                                AuditEventModel.resource_id.in_(
                                    [UUID(account_id), UUID(primary_id), UUID(rotation_id)]
                                )
                            )
                        )
                    )
                assert len(tokens) == 2
                assert all(len(token.token_hash) == 64 for token in tokens)
                assert all(primary_secret not in token.token_hash for token in tokens)
                assert any(token.last_used_at is not None for token in tokens)
                assert primary_secret not in repr([event.details for event in events])
                assert rotation_secret not in repr([event.details for event in events])
            finally:
                await engine.dispose()

        asyncio.run(verify_storage())


def test_expired_and_scope_less_tokens_are_rejected(service_settings: Settings) -> None:
    asyncio.run(_seed(service_settings))
    with TestClient(create_app(service_settings)) as admin:
        _login(admin)
        account = admin.post(
            "/api/v1/service-accounts",
            json={"name": "Token boundaries", "role": "viewer"},
            headers=_csrf(admin),
        ).json()
        issued = admin.post(
            f"/api/v1/service-accounts/{account['id']}/tokens",
            json={
                "name": "Boundary token",
                "scopes": ["dashboards:read"],
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            },
            headers=_csrf(admin),
        ).json()

        async def expire() -> None:
            engine = create_database_engine(service_settings)
            try:
                async with create_session_factory(engine)() as session, session.begin():
                    await session.execute(
                        update(ApiTokenModel)
                        .where(ApiTokenModel.id == UUID(issued["id"]))
                        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
                    )
            finally:
                await engine.dispose()

        asyncio.run(expire())
        with TestClient(create_app(service_settings)) as integration:
            expired = integration.get("/api/v1/dashboards", headers=_bearer(issued["token"]))
            assert expired.status_code == 401

        replacement = admin.post(
            f"/api/v1/service-accounts/{account['id']}/tokens",
            json={"name": "No scope", "scopes": ["dashboards:read"]},
            headers=_csrf(admin),
        ).json()

        async def remove_scope() -> None:
            engine = create_database_engine(service_settings)
            try:
                async with create_session_factory(engine)() as session, session.begin():
                    await session.execute(
                        update(ApiTokenModel)
                        .where(ApiTokenModel.id == UUID(replacement["id"]))
                        .values(scopes=[])
                    )
            finally:
                await engine.dispose()

        asyncio.run(remove_scope())
        with TestClient(create_app(service_settings)) as integration:
            denied = integration.get("/api/v1/dashboards", headers=_bearer(replacement["token"]))
            assert denied.status_code == 403
            assert denied.json()["code"] == "api_token_scope_required"
