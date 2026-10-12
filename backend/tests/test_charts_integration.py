"""Published-snapshot chart API integration tests."""

import asyncio
import hashlib
import os
from collections.abc import Generator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy import select, text

from datepipette.api.dependencies.authentication import CSRF_COOKIE_NAME
from datepipette.application.auth_service import bootstrap_first_admin
from datepipette.config import Settings
from datepipette.infrastructure.database import create_database_engine, create_session_factory
from datepipette.infrastructure.models import (
    AuditEventModel,
    ChartFieldDependencyModel,
    DatasetModel,
    DatasetRefreshRunModel,
    DatasetSnapshotModel,
    DatasetVersionModel,
    PostgresConnectionModel,
    UserModel,
)
from datepipette.infrastructure.repositories.authentication import SqlAlchemyAuthenticationStore
from datepipette.main import create_app

ADMIN_EMAIL = "chart-admin@example.com"
ADMIN_PASSWORD = "correct horse battery staple"


async def _reset(settings: Settings) -> None:
    engine = create_database_engine(settings)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "TRUNCATE chart_field_dependencies, charts, dataset_snapshots, "
                    "dataset_refresh_requests, dataset_refresh_runs, dataset_versions, datasets, "
                    "postgresql_connections, browser_sessions, audit_events, users "
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


@pytest.fixture
def chart_settings(tmp_path: Path) -> Settings:
    database_url = os.environ.get(
        "DATEPIPETTE_TEST_DATABASE_URL",
        "postgresql+psycopg://datepipette:datepipette_dev_only@localhost:5432/datepipette",
    )
    return Settings(
        environment="test",
        docs_enabled=False,
        installation_timezone="Asia/Almaty",
        snapshot_storage_path=tmp_path / "snapshots",
        metadata_database_url=SecretStr(database_url),
    )


@pytest.fixture(autouse=True)
def clean_state(chart_settings: Settings) -> Generator[None, None, None]:
    asyncio.run(_reset(chart_settings))
    yield
    asyncio.run(_reset(chart_settings))


async def _seed_published_dataset(settings: Settings) -> tuple[UUID, UUID, UUID, UUID]:
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        await bootstrap_first_admin(
            SqlAlchemyAuthenticationStore(session_factory),
            ADMIN_EMAIL,
            "Chart Admin",
            ADMIN_PASSWORD,
        )
        now = datetime.now(UTC)
        dataset_id = uuid4()
        connection_id = uuid4()
        draft_id = uuid4()
        version_id = uuid4()
        run_id = uuid4()
        snapshot_id = uuid4()
        region_id = uuid4()
        amount_id = uuid4()
        occurred_at_id = uuid4()
        relation_id = uuid4()
        definition = {
            "kind": "visual",
            "schema_version": 1,
            "source": {
                "relations": [
                    {
                        "id": str(relation_id),
                        "schema": "analytics",
                        "name": "orders",
                        "alias": "orders",
                    }
                ]
            },
            "fields": [
                {
                    "id": str(region_id),
                    "source": {"relation_id": str(relation_id), "column": "region"},
                    "name": "region",
                    "display": {"label": "Region", "format": None, "visible": True},
                },
                {
                    "id": str(amount_id),
                    "source": {"relation_id": str(relation_id), "column": "amount"},
                    "name": "amount",
                    "display": {"label": "Amount", "format": "number_decimal_2", "visible": True},
                },
                {
                    "id": str(occurred_at_id),
                    "source": {"relation_id": str(relation_id), "column": "occurred_at"},
                    "name": "occurred_at",
                    "display": {"label": "Occurred at", "format": None, "visible": True},
                },
            ],
            "joins": [],
        }
        validation = {
            "state": "valid",
            "validated_revision": 1,
            "validated_at": now.isoformat(),
            "compiled_sql": "SELECT region, amount FROM analytics.orders",
            "result_schema": [
                {
                    "field_id": str(region_id),
                    "name": "region",
                    "data_type": "text",
                    "nullable": False,
                },
                {
                    "field_id": str(amount_id),
                    "name": "amount",
                    "data_type": "numeric(12,2)",
                    "nullable": False,
                },
                {
                    "field_id": str(occurred_at_id),
                    "name": "occurred_at",
                    "data_type": "timestamp with time zone",
                    "nullable": False,
                },
            ],
            "plan": None,
            "findings": [],
            "relation_statistics": [],
        }
        object_key = f"datasets/{dataset_id}/snapshots/{snapshot_id}.parquet"
        path = settings.snapshot_storage_path / object_key
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(
            pa.table(
                {
                    "region": ["North", "South", "North"],
                    "amount": [Decimal("10.00"), Decimal("25.00"), Decimal("15.00")],
                    "occurred_at": [
                        datetime(2026, 1, 31, 20, 30, tzinfo=UTC),
                        datetime(2026, 2, 1, 0, 30, tzinfo=UTC),
                        datetime(2026, 2, 1, 20, 30, tzinfo=UTC),
                    ],
                },
                schema=pa.schema(
                    [
                        pa.field("region", pa.string(), nullable=False),
                        pa.field("amount", pa.decimal128(12, 2), nullable=False),
                        pa.field("occurred_at", pa.timestamp("us", tz="UTC"), nullable=False),
                    ]
                ),
            ),
            path,
        )
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        async with session_factory() as session, session.begin():
            admin = await session.scalar(select(UserModel).where(UserModel.email == ADMIN_EMAIL))
            assert admin is not None
            session.add(
                PostgresConnectionModel(
                    id=connection_id,
                    name="Chart source",
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
            dataset = DatasetModel(
                id=dataset_id,
                name="Orders",
                owner_id=admin.id,
                connection_id=connection_id,
                active_version_id=None,
                active_snapshot_id=None,
                created_at=now,
                updated_at=now,
            )
            session.add(dataset)
            await session.flush()
            session.add_all(
                (
                    DatasetVersionModel(
                        id=draft_id,
                        dataset_id=dataset_id,
                        state="draft",
                        revision=1,
                        definition=definition,
                        validation=validation,
                        created_by=admin.id,
                        created_at=now,
                        updated_at=now,
                    ),
                    DatasetVersionModel(
                        id=version_id,
                        dataset_id=dataset_id,
                        state="published",
                        revision=1,
                        definition=definition,
                        validation=validation,
                        created_by=admin.id,
                        created_at=now,
                        updated_at=now,
                    ),
                )
            )
            await session.flush()
            session.add(
                DatasetRefreshRunModel(
                    id=run_id,
                    dataset_id=dataset_id,
                    dataset_version_id=version_id,
                    requested_revision=1,
                    state="succeeded",
                    requested_by=admin.id,
                    trigger_type="manual",
                    schedule_id=None,
                    dispatch_attempts=1,
                    dispatched_at=now,
                    attempt_count=1,
                    error_code=None,
                    next_attempt_at=None,
                    cancel_requested_at=None,
                    cancelled_at=None,
                    lease_expires_at=None,
                    requested_at=now,
                    started_at=now,
                    finished_at=now,
                    updated_at=now,
                )
            )
            await session.flush()
            session.add(
                DatasetSnapshotModel(
                    id=snapshot_id,
                    dataset_id=dataset_id,
                    dataset_version_id=version_id,
                    refresh_run_id=run_id,
                    state="active",
                    storage_backend="local",
                    object_key=object_key,
                    row_count=3,
                    byte_size=path.stat().st_size,
                    checksum_sha256=checksum,
                    schema_document={
                        "fields": [
                            {"name": "region", "storage_name": "region"},
                            {"name": "amount", "storage_name": "amount"},
                            {"name": "occurred_at", "storage_name": "occurred_at"},
                        ]
                    },
                    created_at=now,
                    activated_at=now,
                    superseded_at=None,
                    purged_at=None,
                    cleanup_claimed_at=None,
                )
            )
            await session.flush()
            dataset.active_version_id = version_id
            dataset.active_snapshot_id = snapshot_id
        return dataset_id, region_id, amount_id, occurred_at_id
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


def test_chart_api_persists_updates_queries_and_registers_dependencies(
    chart_settings: Settings,
) -> None:
    dataset_id, region_id, amount_id, occurred_at_id = asyncio.run(
        _seed_published_dataset(chart_settings)
    )
    measure_id = uuid4()
    payload: dict[str, Any] = {
        "name": "Revenue by region",
        "dataset_id": str(dataset_id),
        "definition": {
            "schema_version": 1,
            "chart_type": "bar",
            "dimensions": [{"field_id": str(region_id)}],
            "measures": [
                {
                    "id": str(measure_id),
                    "field_id": str(amount_id),
                    "aggregation": "sum",
                    "label": "Revenue",
                }
            ],
            "filters": [],
            "sorts": [
                {
                    "target_type": "measure",
                    "target_id": str(measure_id),
                    "direction": "desc",
                }
            ],
            "limit": 10,
        },
    }

    with TestClient(create_app(chart_settings)) as client:
        _login(client)
        created = client.post("/api/v1/charts", json=payload, headers=_csrf(client))
        assert created.status_code == 201, created.text
        chart_id = cast(str, created.json()["id"])
        assert created.headers["etag"] == '"chart-1"'

        read = client.get(f"/api/v1/charts/{chart_id}")
        assert read.status_code == 200
        assert read.headers["etag"] == '"chart-1"'

        duplicate = client.post("/api/v1/charts", json=payload, headers=_csrf(client))
        assert duplicate.status_code == 409
        assert duplicate.json()["code"] == "chart_name_conflict"

        invalid_payload = {
            **payload,
            "name": "Invalid aggregation",
            "definition": {
                **cast(dict[str, Any], payload["definition"]),
                "measures": [
                    {
                        "id": str(uuid4()),
                        "field_id": str(region_id),
                        "aggregation": "sum",
                        "label": "Invalid",
                    }
                ],
            },
        }
        invalid = client.post("/api/v1/charts", json=invalid_payload, headers=_csrf(client))
        assert invalid.status_code == 422
        assert invalid.json()["code"] == "chart_definition_invalid"

        queried = client.post(f"/api/v1/charts/{chart_id}/data:query")
        assert queried.status_code == 200, queried.text
        columns = queried.json()["data"]["columns"]
        rows = queried.json()["data"]["rows"]
        assert [item["role"] for item in columns] == ["dimension", "measure"]
        assert [row[columns[0]["key"]] for row in rows] == ["North", "South"]
        assert [row[columns[1]["key"]] for row in rows] == ["25.00", "25.00"]
        assert queried.json()["meta"]["snapshot_id"]

        bucket_measure_id = uuid4()
        bucket_payload = {
            "name": "Daily revenue",
            "dataset_id": str(dataset_id),
            "definition": {
                "schema_version": 2,
                "chart_type": "line",
                "dimensions": [{"field_id": str(occurred_at_id), "time_bucket": "day"}],
                "measures": [
                    {
                        "id": str(bucket_measure_id),
                        "field_id": str(amount_id),
                        "aggregation": "sum",
                        "label": "Revenue",
                    }
                ],
                "filters": [],
                "sorts": [],
                "limit": 10,
            },
        }
        bucket_created = client.post(
            "/api/v1/charts",
            json=bucket_payload,
            headers=_csrf(client),
        )
        assert bucket_created.status_code == 201, bucket_created.text
        bucket_chart_id = bucket_created.json()["id"]
        bucket_query = client.post(f"/api/v1/charts/{bucket_chart_id}/data:query")
        assert bucket_query.status_code == 200, bucket_query.text
        bucket_columns = bucket_query.json()["data"]["columns"]
        bucket_rows = bucket_query.json()["data"]["rows"]
        assert bucket_columns[0]["time_bucket"] == "day"
        assert bucket_columns[0]["timezone"] == "Asia/Almaty"
        assert [row[bucket_columns[0]["key"]] for row in bucket_rows] == [
            "2026-02-01T00:00:00",
            "2026-02-02T00:00:00",
        ]
        assert [row[bucket_columns[1]["key"]] for row in bucket_rows] == [
            "35.00",
            "15.00",
        ]

        update_payload = {"name": "Revenue", "definition": payload["definition"]}
        missing_revision = client.put(
            f"/api/v1/charts/{chart_id}",
            json=update_payload,
            headers=_csrf(client),
        )
        assert missing_revision.status_code == 428
        assert missing_revision.json()["code"] == "chart_revision_required"

        invalid_revision = client.put(
            f"/api/v1/charts/{chart_id}",
            json=update_payload,
            headers={**_csrf(client), "If-Match": "invalid"},
        )
        assert invalid_revision.status_code == 400
        assert invalid_revision.json()["code"] == "invalid_chart_revision"

        updated = client.put(
            f"/api/v1/charts/{chart_id}",
            json=update_payload,
            headers={**_csrf(client), "If-Match": created.headers["etag"]},
        )
        assert updated.status_code == 200, updated.text
        assert updated.headers["etag"] == '"chart-2"'

        stale = client.put(
            f"/api/v1/charts/{chart_id}",
            json=update_payload,
            headers={**_csrf(client), "If-Match": created.headers["etag"]},
        )
        assert stale.status_code == 412
        listing = client.get("/api/v1/charts")
        assert listing.status_code == 200
        assert [item["id"] for item in listing.json()["data"]] == [
            chart_id,
            bucket_chart_id,
        ]

        invalid_cursor = client.get("/api/v1/charts", params={"cursor": "bad!"})
        assert invalid_cursor.status_code == 400
        assert invalid_cursor.json()["code"] == "invalid_chart_cursor"

    async def verify_metadata() -> None:
        engine = create_database_engine(chart_settings)
        try:
            async with create_session_factory(engine)() as session:
                dependencies = list(
                    await session.scalars(
                        select(ChartFieldDependencyModel).where(
                            ChartFieldDependencyModel.chart_id == UUID(chart_id)
                        )
                    )
                )
                actions = list(
                    await session.scalars(
                        select(AuditEventModel.action).where(
                            AuditEventModel.resource_id == UUID(chart_id)
                        )
                    )
                )
            assert {item.field_id for item in dependencies} == {region_id, amount_id}
            assert {"chart.created", "chart.updated", "chart.queried"}.issubset(actions)
        finally:
            await engine.dispose()

    asyncio.run(verify_metadata())
