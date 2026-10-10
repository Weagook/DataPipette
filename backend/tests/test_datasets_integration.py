"""Visual dataset draft API integration tests."""

import asyncio
from collections.abc import Generator
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from redis.asyncio import Redis
from sqlalchemy import select, text

from datepipette.api.dependencies.authentication import CSRF_COOKIE_NAME
from datepipette.application.auth_service import bootstrap_first_admin
from datepipette.application.connection_access import ConnectionAccessAuthorizer
from datepipette.application.refresh_worker import RefreshWorkerService
from datepipette.config import Settings
from datepipette.infrastructure.crypto import AesGcmSecretCipher
from datepipette.infrastructure.database import create_database_engine, create_session_factory
from datepipette.infrastructure.models import (
    AuditEventModel,
    DatasetRefreshRunModel,
    DatasetVersionModel,
)
from datepipette.infrastructure.parquet_snapshots import LocalParquetSnapshotExecutor
from datepipette.infrastructure.repositories.authentication import SqlAlchemyAuthenticationStore
from datepipette.infrastructure.repositories.connection_access import (
    SqlAlchemyConnectionAccessStore,
)
from datepipette.infrastructure.repositories.refresh_runs import SqlAlchemyRefreshRunStore
from datepipette.main import create_app

TEST_SETTINGS = Settings(environment="test", docs_enabled=False)
ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "correct horse battery staple"


async def _reset() -> None:
    engine = create_database_engine(TEST_SETTINGS)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "TRUNCATE dataset_versions, datasets, postgresql_connections, "
                    "browser_sessions, audit_events, users RESTART IDENTITY CASCADE"
                )
            )
    finally:
        await engine.dispose()
    redis = Redis.from_url(TEST_SETTINGS.redis_url.get_secret_value(), decode_responses=True)
    try:
        await redis.flushdb()
    finally:
        await redis.aclose()


async def _bootstrap() -> None:
    engine = create_database_engine(TEST_SETTINGS)
    try:
        await bootstrap_first_admin(
            SqlAlchemyAuthenticationStore(create_session_factory(engine)),
            ADMIN_EMAIL,
            "Admin",
            ADMIN_PASSWORD,
        )
    finally:
        await engine.dispose()


@pytest.fixture(autouse=True)
def clean_state() -> Generator[None, None, None]:
    asyncio.run(_reset())
    yield
    asyncio.run(_reset())


def _login(client: TestClient, email: str = ADMIN_EMAIL, password: str = ADMIN_PASSWORD) -> None:
    response = client.post(
        "/api/v1/auth/sessions",
        json={"email": email, "password": password},
    )
    assert response.status_code == 201


def _csrf(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies[CSRF_COOKIE_NAME]}


def _create_connection(client: TestClient) -> str:
    response = client.post(
        "/api/v1/connections",
        json={
            "name": "Offline source",
            "host": "source.invalid",
            "port": 5432,
            "database_name": "analytics",
            "username": "readonly",
            "password": "source-secret",
            "ssl_mode": "disable",
        },
        headers=_csrf(client),
    )
    assert response.status_code == 201
    return cast(str, response.json()["id"])


def _definition() -> dict[str, Any]:
    relation_id = str(uuid4())
    return {
        "schema_version": 1,
        "source": {
            "relations": [
                {
                    "id": relation_id,
                    "schema": "crm",
                    "name": "customers",
                    "alias": "customers",
                }
            ]
        },
        "fields": [
            {
                "id": str(uuid4()),
                "source": {"relation_id": relation_id, "column": "id"},
                "name": "customer_id",
                "display": {"label": "Customer ID", "format": None, "visible": True},
            },
            {
                "id": str(uuid4()),
                "source": {"relation_id": relation_id, "column": "created_at"},
                "name": "created_at",
                "display": {
                    "label": "Created at",
                    "format": "date_dd_mm_yyyy",
                    "visible": True,
                },
            },
        ],
        "joins": [],
    }


def _joined_definition() -> dict[str, Any]:
    definition = _definition()
    customers_id = definition["source"]["relations"][0]["id"]
    orders_id = str(uuid4())
    definition["schema_version"] = 2
    definition["source"]["relations"].append(
        {"id": orders_id, "schema": "crm", "name": "orders", "alias": "orders"}
    )
    definition["fields"].append(
        {
            "id": str(uuid4()),
            "source": {"relation_id": orders_id, "column": "amount"},
            "name": "order_amount",
            "display": {"label": "Order amount", "format": None, "visible": True},
        }
    )
    definition["joins"] = [
        {
            "id": str(uuid4()),
            "type": "left",
            "left_relation_id": customers_id,
            "right_relation_id": orders_id,
            "conditions": [{"left_column": "id", "right_column": "customer_id"}],
        }
    ]
    return definition


def _create_dataset(
    client: TestClient,
    connection_id: str,
    *,
    name: str = "Customers",
    definition: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str]:
    response = client.post(
        "/api/v1/datasets",
        json={
            "name": name,
            "connection_id": connection_id,
            "definition": definition or _definition(),
        },
        headers=_csrf(client),
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json()), response.headers["etag"]


async def _mark_valid(dataset_id: str) -> None:
    engine = create_database_engine(TEST_SETTINGS)
    try:
        async with create_session_factory(engine)() as session, session.begin():
            draft = await session.scalar(
                select(DatasetVersionModel).where(
                    DatasetVersionModel.dataset_id == UUID(dataset_id)
                )
            )
            assert draft is not None
            fields = cast(list[dict[str, Any]], draft.definition["fields"])
            draft.validation = {
                "state": "valid",
                "validated_revision": draft.revision,
                "validated_at": datetime.now(UTC).isoformat(),
                "compiled_sql": "SELECT 1",
                "result_schema": [
                    {
                        "field_id": field["id"],
                        "name": field["name"],
                        "data_type": "bigint",
                        "nullable": True,
                    }
                    for field in fields
                ],
                "plan": None,
                "findings": [],
            }
    finally:
        await engine.dispose()


async def _execute_refresh(run_id: str, storage_root: Path) -> str | None:
    engine = create_database_engine(TEST_SETTINGS)
    session_factory = create_session_factory(engine)
    store = SqlAlchemyRefreshRunStore(session_factory)
    authorizer = ConnectionAccessAuthorizer(SqlAlchemyConnectionAccessStore(session_factory))
    try:
        worker = RefreshWorkerService(
            store,
            LocalParquetSnapshotExecutor(
                session_factory,
                AesGcmSecretCipher(TEST_SETTINGS.connection_encryption_key.get_secret_value()),
                storage_root,
                connect_timeout_seconds=1,
                statement_timeout_seconds=1,
                batch_rows=100,
                authorizer=authorizer,
            ),
        )
        await worker.execute(UUID(run_id))
        async with session_factory() as session:
            run = await session.get(DatasetRefreshRunModel, UUID(run_id))
            assert run is not None
            return run.error_code
    finally:
        await engine.dispose()


def test_dataset_draft_round_trip_revision_and_redacted_audit() -> None:
    asyncio.run(_bootstrap())
    original_definition = _definition()
    with TestClient(create_app(settings=TEST_SETTINGS)) as client:
        _login(client)
        connection_id = _create_connection(client)
        created, etag = _create_dataset(
            client,
            connection_id,
            definition=original_definition,
        )
        dataset_id = created["id"]
        assert etag == '"draft-1"'
        assert created["connection_id"] == connection_id
        assert created["active_version_id"] is None
        assert created["draft_revision"] == 1

        read = client.get(f"/api/v1/datasets/{dataset_id}")
        assert read.status_code == 200
        draft = client.get(f"/api/v1/datasets/{dataset_id}/draft")
        assert draft.status_code == 200
        assert draft.headers["etag"] == '"draft-1"'
        assert draft.json()["definition"] == original_definition

        changed_definition = deepcopy(original_definition)
        changed_definition["fields"][1]["display"]["label"] = "Registration date"
        missing_precondition = client.put(
            f"/api/v1/datasets/{dataset_id}/draft",
            json={"definition": changed_definition},
            headers=_csrf(client),
        )
        assert missing_precondition.status_code == 428
        invalid_precondition = client.put(
            f"/api/v1/datasets/{dataset_id}/draft",
            json={"definition": changed_definition},
            headers={**_csrf(client), "If-Match": "revision-1"},
        )
        assert invalid_precondition.status_code == 400

        replaced = client.put(
            f"/api/v1/datasets/{dataset_id}/draft",
            json={"definition": changed_definition},
            headers={**_csrf(client), "If-Match": etag},
        )
        assert replaced.status_code == 200, replaced.text
        assert replaced.json()["revision"] == 2
        assert replaced.headers["etag"] == '"draft-2"'

        stale = client.put(
            f"/api/v1/datasets/{dataset_id}/draft",
            json={"definition": original_definition},
            headers={**_csrf(client), "If-Match": etag},
        )
        assert stale.status_code == 409
        assert stale.json()["code"] == "dataset_revision_conflict"

        idempotent = client.put(
            f"/api/v1/datasets/{dataset_id}/draft",
            json={"definition": changed_definition},
            headers={**_csrf(client), "If-Match": '"draft-2"'},
        )
        assert idempotent.status_code == 200
        assert idempotent.json()["revision"] == 2

        renamed = client.patch(
            f"/api/v1/datasets/{dataset_id}",
            json={"name": "CRM customers"},
            headers=_csrf(client),
        )
        assert renamed.status_code == 200
        assert renamed.json()["name"] == "CRM customers"

    async def inspect() -> tuple[dict[str, Any], list[AuditEventModel]]:
        engine = create_database_engine(TEST_SETTINGS)
        try:
            async with create_session_factory(engine)() as session:
                definition = await session.scalar(select(DatasetVersionModel.definition))
                events = list(
                    (
                        await session.scalars(
                            select(AuditEventModel).where(
                                AuditEventModel.resource_type == "dataset"
                            )
                        )
                    ).all()
                )
                assert definition is not None
                return definition, events
        finally:
            await engine.dispose()

    stored_definition, events = asyncio.run(inspect())
    assert stored_definition["fields"][1]["display"]["label"] == "Registration date"
    assert {event.action for event in events} == {
        "dataset.created",
        "dataset.draft_replaced",
        "dataset.renamed",
    }
    assert all("definition" not in event.details for event in events)
    assert "Registration date" not in repr(events)


def test_dataset_collection_conflicts_validation_and_admin_boundary() -> None:
    asyncio.run(_bootstrap())
    with TestClient(create_app(settings=TEST_SETTINGS)) as client:
        assert client.get("/api/v1/datasets").status_code == 401
        _login(client)
        connection_id = _create_connection(client)
        first, _ = _create_dataset(client, connection_id, name="Customers")
        second, _ = _create_dataset(client, connection_id, name="Orders")

        duplicate = client.post(
            "/api/v1/datasets",
            json={
                "name": " customers ",
                "connection_id": connection_id,
                "definition": _definition(),
            },
            headers=_csrf(client),
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["code"] == "dataset_name_conflict"
        rename_conflict = client.patch(
            f"/api/v1/datasets/{second['id']}",
            json={"name": "CUSTOMERS"},
            headers=_csrf(client),
        )
        assert rename_conflict.status_code == 409

        page_one = client.get("/api/v1/datasets", params={"limit": 1})
        assert page_one.status_code == 200
        assert len(page_one.json()["data"]) == 1
        cursor = page_one.json()["meta"]["next_cursor"]
        page_two = client.get(
            "/api/v1/datasets",
            params={"limit": 1, "cursor": cursor},
        )
        assert page_two.status_code == 200
        assert {page_one.json()["data"][0]["id"], page_two.json()["data"][0]["id"]} == {
            first["id"],
            second["id"],
        }
        assert client.get("/api/v1/datasets", params={"cursor": "%%%"}).status_code == 400

        unknown_connection = client.post(
            "/api/v1/datasets",
            json={
                "name": "Missing source",
                "connection_id": str(uuid4()),
                "definition": _definition(),
            },
            headers=_csrf(client),
        )
        assert unknown_connection.status_code == 404
        assert unknown_connection.json()["code"] == "connection_not_found"

        invalid_definition = _definition()
        invalid_definition["fields"][1]["name"] = "CUSTOMER_ID"
        invalid = client.post(
            "/api/v1/datasets",
            json={
                "name": "Invalid",
                "connection_id": connection_id,
                "definition": invalid_definition,
            },
            headers=_csrf(client),
        )
        assert invalid.status_code == 422
        assert invalid.json()["code"] == "dataset_definition_invalid"

        disconnected_definition = _joined_definition()
        disconnected_definition["joins"] = []
        disconnected = client.post(
            "/api/v1/datasets",
            json={
                "name": "Disconnected",
                "connection_id": connection_id,
                "definition": disconnected_definition,
            },
            headers=_csrf(client),
        )
        assert disconnected.status_code == 422
        assert disconnected.json()["code"] == "dataset_definition_invalid"

        missing_id = uuid4()
        assert client.get(f"/api/v1/datasets/{missing_id}").status_code == 404
        assert client.get(f"/api/v1/datasets/{missing_id}/draft").status_code == 404
        missing_rename = client.patch(
            f"/api/v1/datasets/{missing_id}",
            json={"name": "Missing"},
            headers=_csrf(client),
        )
        assert missing_rename.status_code == 404

        created_user = client.post(
            "/api/v1/users",
            json={
                "email": "analyst@example.com",
                "display_name": "Analyst",
                "password": "analyst password for tests",
                "role": "analyst",
            },
            headers=_csrf(client),
        )
        assert created_user.status_code == 201

    with TestClient(create_app(settings=TEST_SETTINGS)) as analyst_client:
        _login(
            analyst_client,
            email="analyst@example.com",
            password="analyst password for tests",
        )
        visible = analyst_client.get("/api/v1/datasets")
        assert visible.status_code == 200
        assert visible.json()["data"] == []


def test_analyst_authors_only_owned_datasets_and_revocation_blocks_source_operations(
    tmp_path: Path,
) -> None:
    asyncio.run(_bootstrap())
    user_password = "analyst source access password"
    with TestClient(create_app(settings=TEST_SETTINGS)) as admin:
        _login(admin)
        connection_id = _create_connection(admin)
        analyst_response = admin.post(
            "/api/v1/users",
            json={
                "email": "owner-analyst@example.com",
                "display_name": "Owner analyst",
                "password": user_password,
                "role": "analyst",
            },
            headers=_csrf(admin),
        )
        assert analyst_response.status_code == 201
        analyst_id = cast(str, analyst_response.json()["id"])
        other_response = admin.post(
            "/api/v1/users",
            json={
                "email": "other-analyst@example.com",
                "display_name": "Other analyst",
                "password": user_password,
                "role": "analyst",
            },
            headers=_csrf(admin),
        )
        assert other_response.status_code == 201
        viewer_response = admin.post(
            "/api/v1/users",
            json={
                "email": "dataset-viewer@example.com",
                "display_name": "Dataset viewer",
                "password": user_password,
                "role": "viewer",
            },
            headers=_csrf(admin),
        )
        assert viewer_response.status_code == 201

        grant = admin.post(
            f"/api/v1/connections/{connection_id}/grants",
            json={
                "principal_type": "user",
                "principal_id": analyst_id,
                "scope_type": "table",
                "schema_name": "crm",
                "relation_name": "customers",
            },
            headers=_csrf(admin),
        )
        assert grant.status_code == 201, grant.text
        grant_id = cast(str, grant.json()["id"])
        admin_dataset, _ = _create_dataset(admin, connection_id, name="Admin private")

    with TestClient(create_app(settings=TEST_SETTINGS)) as analyst:
        _login(analyst, email="owner-analyst@example.com", password=user_password)
        blocked_join = analyst.post(
            "/api/v1/datasets",
            json={
                "name": "Unauthorized joined dataset",
                "connection_id": connection_id,
                "definition": _joined_definition(),
            },
            headers=_csrf(analyst),
        )
        assert blocked_join.status_code == 404
        created, etag = _create_dataset(analyst, connection_id, name="Analyst private")
        dataset_id = cast(str, created["id"])
        listed = analyst.get("/api/v1/datasets")
        assert [item["id"] for item in listed.json()["data"]] == [dataset_id]
        assert analyst.get(f"/api/v1/datasets/{admin_dataset['id']}").status_code == 404
        assert analyst.get(f"/api/v1/datasets/{dataset_id}").status_code == 200
        assert analyst.get(f"/api/v1/datasets/{dataset_id}/draft").status_code == 200

        changed = _definition()
        changed["fields"][0]["display"]["label"] = "Identifier"
        replaced = analyst.put(
            f"/api/v1/datasets/{dataset_id}/draft",
            json={"definition": changed},
            headers={**_csrf(analyst), "If-Match": etag},
        )
        assert replaced.status_code == 200, replaced.text
        etag = replaced.headers["etag"]

        validation = analyst.post(
            f"/api/v1/datasets/{dataset_id}/draft/validations",
            headers={**_csrf(analyst), "If-Match": etag},
        )
        assert validation.status_code == 502
        asyncio.run(_mark_valid(dataset_id))
        preview = analyst.post(
            f"/api/v1/datasets/{dataset_id}/draft/previews",
            json={"limit": 10},
            headers={**_csrf(analyst), "If-Match": etag},
        )
        assert preview.status_code == 502
        refresh = analyst.post(
            f"/api/v1/datasets/{dataset_id}/refresh-runs",
            headers={
                **_csrf(analyst),
                "If-Match": etag,
                "Idempotency-Key": "before-revoke",
            },
        )
        assert refresh.status_code == 202, refresh.text
        refresh_id = cast(str, refresh.json()["id"])

    with TestClient(create_app(settings=TEST_SETTINGS)) as other:
        _login(other, email="other-analyst@example.com", password=user_password)
        assert other.get(f"/api/v1/datasets/{dataset_id}").status_code == 404
        assert other.get(f"/api/v1/datasets/{dataset_id}/draft").status_code == 404

    with TestClient(create_app(settings=TEST_SETTINGS)) as viewer:
        _login(viewer, email="dataset-viewer@example.com", password=user_password)
        blocked = viewer.post(
            "/api/v1/datasets",
            json={
                "name": "Viewer dataset",
                "connection_id": connection_id,
                "definition": _definition(),
            },
            headers=_csrf(viewer),
        )
        assert blocked.status_code == 404
        assert viewer.get("/api/v1/datasets").json()["data"] == []

    with TestClient(create_app(settings=TEST_SETTINGS)) as admin:
        _login(admin)
        revoked = admin.delete(
            f"/api/v1/connections/{connection_id}/grants/{grant_id}",
            headers=_csrf(admin),
        )
        assert revoked.status_code == 204

    assert asyncio.run(_execute_refresh(refresh_id, tmp_path)) == "connection_access_revoked"

    with TestClient(create_app(settings=TEST_SETTINGS)) as analyst:
        _login(analyst, email="owner-analyst@example.com", password=user_password)
        assert analyst.get(f"/api/v1/datasets/{dataset_id}").status_code == 200
        assert analyst.get(f"/api/v1/datasets/{dataset_id}/draft").status_code == 200
        blocked_replace = analyst.put(
            f"/api/v1/datasets/{dataset_id}/draft",
            json={"definition": changed},
            headers={**_csrf(analyst), "If-Match": etag},
        )
        assert blocked_replace.status_code == 404
        blocked_validation = analyst.post(
            f"/api/v1/datasets/{dataset_id}/draft/validations",
            headers={**_csrf(analyst), "If-Match": etag},
        )
        assert blocked_validation.status_code == 404
        blocked_preview = analyst.post(
            f"/api/v1/datasets/{dataset_id}/draft/previews",
            json={"limit": 10},
            headers={**_csrf(analyst), "If-Match": etag},
        )
        assert blocked_preview.status_code == 404
        blocked_refresh = analyst.post(
            f"/api/v1/datasets/{dataset_id}/refresh-runs",
            headers={
                **_csrf(analyst),
                "If-Match": etag,
                "Idempotency-Key": "after-revoke",
            },
        )
        assert blocked_refresh.status_code == 404
        blocked_create = analyst.post(
            "/api/v1/datasets",
            json={
                "name": "Blocked dataset",
                "connection_id": connection_id,
                "definition": _definition(),
            },
            headers=_csrf(analyst),
        )
        assert blocked_create.status_code == 404
