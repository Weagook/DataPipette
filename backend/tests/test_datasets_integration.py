"""Visual dataset draft API integration tests."""

import asyncio
from collections.abc import Generator
from copy import deepcopy
from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from redis.asyncio import Redis
from sqlalchemy import select, text

from datepipette.api.dependencies.authentication import CSRF_COOKIE_NAME
from datepipette.application.auth_service import bootstrap_first_admin
from datepipette.config import Settings
from datepipette.infrastructure.database import create_database_engine, create_session_factory
from datepipette.infrastructure.models import AuditEventModel, DatasetVersionModel
from datepipette.infrastructure.repositories.authentication import SqlAlchemyAuthenticationStore
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
        forbidden = analyst_client.get("/api/v1/datasets")
        assert forbidden.status_code == 403
        assert forbidden.json()["code"] == "administrator_required"
