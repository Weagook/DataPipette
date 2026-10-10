"""Live PostgreSQL catalog API integration tests."""

import asyncio
from collections.abc import Generator
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from redis.asyncio import Redis
from sqlalchemy import select, text, update

from datepipette.api.dependencies.authentication import CSRF_COOKIE_NAME
from datepipette.application.auth_service import bootstrap_first_admin
from datepipette.config import Settings
from datepipette.infrastructure.database import create_database_engine, create_session_factory
from datepipette.infrastructure.models import AuditEventModel, PostgresConnectionModel
from datepipette.infrastructure.repositories.authentication import SqlAlchemyAuthenticationStore
from datepipette.main import create_app

TEST_SETTINGS = Settings(environment="test", docs_enabled=False)
ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "correct horse battery staple"
SOURCE_PASSWORD = "datepipette_dev_only"
SCHEMA = "catalog_fixture"
READER_ROLE = "datepipette_catalog_reader"
READER_PASSWORD = "catalog-reader-password"


async def _reset() -> None:
    engine = create_database_engine(TEST_SETTINGS)
    try:
        async with engine.begin() as connection:
            await connection.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
            await connection.execute(
                text(
                    f"""
                    DO $$
                    BEGIN
                        IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '{READER_ROLE}')
                        THEN
                            EXECUTE 'DROP OWNED BY {READER_ROLE}';
                            EXECUTE 'DROP ROLE {READER_ROLE}';
                        END IF;
                    END
                    $$
                    """
                )
            )
            await connection.execute(
                text(
                    "TRUNCATE postgresql_connections, browser_sessions, audit_events, users "
                    "RESTART IDENTITY CASCADE"
                )
            )
    finally:
        await engine.dispose()
    redis = Redis.from_url(TEST_SETTINGS.redis_url.get_secret_value(), decode_responses=True)
    try:
        await redis.flushdb()
    finally:
        await redis.aclose()


async def _bootstrap_and_create_source_objects() -> None:
    engine = create_database_engine(TEST_SETTINGS)
    try:
        await bootstrap_first_admin(
            SqlAlchemyAuthenticationStore(create_session_factory(engine)),
            ADMIN_EMAIL,
            "Admin",
            ADMIN_PASSWORD,
        )
        async with engine.begin() as connection:
            await connection.execute(text(f"CREATE SCHEMA {SCHEMA}"))
            await connection.execute(
                text(
                    f"""
                    CREATE TABLE {SCHEMA}.customers (
                        id bigint PRIMARY KEY,
                        email text NOT NULL,
                        created_at timestamp with time zone
                    )
                    """
                )
            )
            await connection.execute(
                text(
                    f"""
                    CREATE TABLE {SCHEMA}.orders (
                        id bigint PRIMARY KEY,
                        customer_id bigint NOT NULL
                            REFERENCES {SCHEMA}.customers(id),
                        amount numeric(12,2)
                    )
                    """
                )
            )
            await connection.execute(
                text(
                    f"CREATE VIEW {SCHEMA}.order_amounts AS SELECT id, amount FROM {SCHEMA}.orders"
                )
            )
            await connection.execute(
                text(f"CREATE ROLE {READER_ROLE} LOGIN PASSWORD '{READER_PASSWORD}'")
            )
            await connection.execute(
                text(f"GRANT CONNECT ON DATABASE datepipette TO {READER_ROLE}")
            )
            await connection.execute(text(f"GRANT USAGE ON SCHEMA {SCHEMA} TO {READER_ROLE}"))
            await connection.execute(text(f"GRANT SELECT ON {SCHEMA}.orders TO {READER_ROLE}"))
    finally:
        await engine.dispose()


async def _corrupt_connection_secret(connection_id: str) -> None:
    engine = create_database_engine(TEST_SETTINGS)
    try:
        async with create_session_factory(engine)() as session, session.begin():
            await session.execute(
                update(PostgresConnectionModel)
                .where(PostgresConnectionModel.id == UUID(connection_id))
                .values(encrypted_password="v1.corrupt")
            )
    finally:
        await engine.dispose()


async def _connection_grant_audit_events() -> list[AuditEventModel]:
    engine = create_database_engine(TEST_SETTINGS)
    try:
        async with create_session_factory(engine)() as session:
            return list(
                (
                    await session.scalars(
                        select(AuditEventModel)
                        .where(AuditEventModel.action.like("connection.grant_%"))
                        .order_by(AuditEventModel.occurred_at, AuditEventModel.id)
                    )
                ).all()
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


def _headers(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies[CSRF_COOKIE_NAME]}


def _create_connection(
    client: TestClient,
    *,
    name: str = "Catalog source",
    username: str = "datepipette",
    password: str = SOURCE_PASSWORD,
) -> str:
    response = client.post(
        "/api/v1/connections",
        json={
            "name": name,
            "host": "127.0.0.1",
            "port": 5432,
            "database_name": "datepipette",
            "username": username,
            "password": password,
            "ssl_mode": "disable",
        },
        headers=_headers(client),
    )
    assert response.status_code == 201, response.text
    return cast(str, response.json()["id"])


def _create_user(client: TestClient, *, email: str, role: str) -> str:
    response = client.post(
        "/api/v1/users",
        json={
            "email": email,
            "display_name": email.split("@", maxsplit=1)[0],
            "password": "connection visibility password",
            "role": role,
        },
        headers=_headers(client),
    )
    assert response.status_code == 201, response.text
    return cast(str, response.json()["id"])


def _grant(
    client: TestClient,
    connection_id: str,
    *,
    principal_type: str,
    principal_id: str,
    scope_type: str,
    schema_name: str | None = None,
    relation_name: str | None = None,
) -> dict[str, object]:
    response = client.post(
        f"/api/v1/connections/{connection_id}/grants",
        json={
            "principal_type": principal_type,
            "principal_id": principal_id,
            "scope_type": scope_type,
            "schema_name": schema_name,
            "relation_name": relation_name,
        },
        headers=_headers(client),
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, object], response.json())


def test_catalog_lists_live_objects_and_describes_keys() -> None:
    asyncio.run(_bootstrap_and_create_source_objects())
    with TestClient(create_app(settings=TEST_SETTINGS)) as client:
        _login(client)
        connection_id = _create_connection(client)

        unknown = client.get(f"/api/v1/connections/{uuid4()}/catalog/schemas")
        assert unknown.status_code == 404
        assert unknown.json()["code"] == "connection_not_found"

        schemas = client.get(f"/api/v1/connections/{connection_id}/catalog/schemas")
        assert schemas.status_code == 200, schemas.text
        schema_names = [item["name"] for item in schemas.json()["data"]]
        assert SCHEMA in schema_names
        assert "pg_catalog" not in schema_names
        assert "information_schema" not in schema_names

        system_schemas = client.get(
            f"/api/v1/connections/{connection_id}/catalog/schemas",
            params={"include_system": True, "limit": 100},
        )
        assert system_schemas.status_code == 200
        assert "pg_catalog" in [item["name"] for item in system_schemas.json()["data"]]

        first_page = client.get(
            f"/api/v1/connections/{connection_id}/catalog/schemas/{SCHEMA}/relations",
            params={"limit": 1},
        )
        assert first_page.status_code == 200
        assert len(first_page.json()["data"]) == 1
        cursor = first_page.json()["meta"]["next_cursor"]
        assert cursor is not None
        second_page = client.get(
            f"/api/v1/connections/{connection_id}/catalog/schemas/{SCHEMA}/relations",
            params={"limit": 100, "cursor": cursor},
        )
        assert second_page.status_code == 200
        relation_names = {
            first_page.json()["data"][0]["name"],
            *(item["name"] for item in second_page.json()["data"]),
        }
        assert relation_names == {"customers", "order_amounts", "orders"}
        assert {item["kind"] for item in second_page.json()["data"]} <= {"table", "view"}

        detail = client.get(
            f"/api/v1/connections/{connection_id}/catalog/schemas/{SCHEMA}/relations/orders"
        )
        assert detail.status_code == 200, detail.text
        payload = detail.json()
        assert payload["kind"] == "table"
        assert payload["primary_key"] == ["id"]
        assert payload["columns"] == [
            {
                "name": "id",
                "ordinal_position": 1,
                "data_type": "bigint",
                "nullable": False,
            },
            {
                "name": "customer_id",
                "ordinal_position": 2,
                "data_type": "bigint",
                "nullable": False,
            },
            {
                "name": "amount",
                "ordinal_position": 3,
                "data_type": "numeric(12,2)",
                "nullable": True,
            },
        ]
        assert payload["foreign_keys"] == [
            {
                "name": "orders_customer_id_fkey",
                "source_columns": ["customer_id"],
                "target_schema": SCHEMA,
                "target_relation": "customers",
                "target_columns": ["id"],
            }
        ]

        restricted_id = _create_connection(
            client,
            name="Restricted catalog source",
            username=READER_ROLE,
            password=READER_PASSWORD,
        )
        restricted = client.get(
            f"/api/v1/connections/{restricted_id}/catalog/schemas/{SCHEMA}/relations"
        )
        assert restricted.status_code == 200, restricted.text
        assert restricted.json()["data"] == [
            {"schema_name": SCHEMA, "name": "orders", "kind": "table"}
        ]
        restricted_detail = client.get(
            f"/api/v1/connections/{restricted_id}/catalog/schemas/{SCHEMA}/relations/orders"
        )
        assert restricted_detail.status_code == 200
        assert restricted_detail.json()["foreign_keys"] == []


def test_catalog_returns_safe_errors_and_conceals_ungranted_connections() -> None:
    asyncio.run(_bootstrap_and_create_source_objects())
    with TestClient(create_app(settings=TEST_SETTINGS)) as client:
        assert client.get(f"/api/v1/connections/{uuid4()}/catalog/schemas").status_code == 401
        _login(client)
        connection_id = _create_connection(client)

        invalid_cursor = client.get(
            f"/api/v1/connections/{connection_id}/catalog/schemas",
            params={"cursor": "%%%"},
        )
        assert invalid_cursor.status_code == 400
        assert invalid_cursor.json()["code"] == "invalid_cursor"

        missing = client.get(
            f"/api/v1/connections/{connection_id}/catalog/schemas/{SCHEMA}/relations/missing"
        )
        assert missing.status_code == 404
        assert missing.json()["code"] == "catalog_object_not_found"

        changed = client.patch(
            f"/api/v1/connections/{connection_id}",
            json={"password": "not-the-source-password"},
            headers=_headers(client),
        )
        assert changed.status_code == 200
        unavailable = client.get(f"/api/v1/connections/{connection_id}/catalog/schemas")
        assert unavailable.status_code == 502
        assert unavailable.json()["code"] == "source_catalog_unavailable"
        assert "not-the-source-password" not in unavailable.text

        asyncio.run(_corrupt_connection_secret(connection_id))
        secret_unavailable = client.get(f"/api/v1/connections/{connection_id}/catalog/schemas")
        assert secret_unavailable.status_code == 409
        assert secret_unavailable.json()["code"] == "connection_secret_unavailable"

        created_user = client.post(
            "/api/v1/users",
            json={
                "email": "analyst@example.com",
                "display_name": "Analyst",
                "password": "analyst password for tests",
                "role": "analyst",
            },
            headers=_headers(client),
        )
        assert created_user.status_code == 201

    with TestClient(create_app(settings=TEST_SETTINGS)) as analyst_client:
        _login(
            analyst_client,
            email="analyst@example.com",
            password="analyst password for tests",
        )
        concealed = analyst_client.get(f"/api/v1/connections/{connection_id}/catalog/schemas")
        assert concealed.status_code == 404
        assert concealed.json()["code"] == "connection_not_found"


def test_connection_scopes_union_filter_foreign_keys_and_revoke_immediately() -> None:
    asyncio.run(_bootstrap_and_create_source_objects())
    with TestClient(create_app(settings=TEST_SETTINGS)) as admin:
        _login(admin)
        connection_id = _create_connection(admin)
        analyst_id = _create_user(admin, email="scoped-analyst@example.com", role="analyst")
        viewer_id = _create_user(admin, email="scoped-viewer@example.com", role="viewer")
        group = admin.post(
            "/api/v1/groups",
            json={"name": "Catalog team"},
            headers=_headers(admin),
        )
        assert group.status_code == 201
        group_id = cast(str, group.json()["id"])
        for user_id in (analyst_id, viewer_id):
            membership = admin.put(
                f"/api/v1/groups/{group_id}/members/{user_id}",
                headers=_headers(admin),
            )
            assert membership.status_code == 200

        missing_csrf = admin.post(
            f"/api/v1/connections/{connection_id}/grants",
            json={
                "principal_type": "user",
                "principal_id": analyst_id,
                "scope_type": "all",
            },
        )
        assert missing_csrf.status_code == 403

        orders_grant = _grant(
            admin,
            connection_id,
            principal_type="user",
            principal_id=analyst_id,
            scope_type="table",
            schema_name=SCHEMA,
            relation_name="orders",
        )
        repeated = _grant(
            admin,
            connection_id,
            principal_type="user",
            principal_id=analyst_id,
            scope_type="table",
            schema_name=SCHEMA,
            relation_name="orders",
        )
        assert repeated["id"] == orders_grant["id"]
        _grant(
            admin,
            connection_id,
            principal_type="group",
            principal_id=group_id,
            scope_type="table",
            schema_name=SCHEMA,
            relation_name="customers",
        )
        assert len(admin.get(f"/api/v1/connections/{connection_id}/grants").json()["data"]) == 2

        invalid_scope = admin.post(
            f"/api/v1/connections/{connection_id}/grants",
            json={
                "principal_type": "user",
                "principal_id": analyst_id,
                "scope_type": "all",
                "schema_name": SCHEMA,
            },
            headers=_headers(admin),
        )
        assert invalid_scope.status_code == 422
        invalid_viewer = admin.post(
            f"/api/v1/connections/{connection_id}/grants",
            json={
                "principal_type": "user",
                "principal_id": viewer_id,
                "scope_type": "all",
            },
            headers=_headers(admin),
        )
        assert invalid_viewer.status_code == 404

    with TestClient(create_app(settings=TEST_SETTINGS)) as analyst:
        _login(
            analyst,
            email="scoped-analyst@example.com",
            password="connection visibility password",
        )
        listed = analyst.get("/api/v1/connections")
        assert [item["id"] for item in listed.json()["data"]] == [connection_id]
        assert analyst.get(f"/api/v1/connections/{connection_id}").status_code == 200
        assert analyst.get(f"/api/v1/connections/{connection_id}/grants").status_code == 403
        assert (
            analyst.post(
                f"/api/v1/connections/{connection_id}/tests",
                headers=_headers(analyst),
            ).status_code
            == 403
        )
        schemas = analyst.get(f"/api/v1/connections/{connection_id}/catalog/schemas")
        assert schemas.json()["data"] == [{"name": SCHEMA}]
        relations = analyst.get(
            f"/api/v1/connections/{connection_id}/catalog/schemas/{SCHEMA}/relations"
        )
        assert {item["name"] for item in relations.json()["data"]} == {"customers", "orders"}
        order_detail = analyst.get(
            f"/api/v1/connections/{connection_id}/catalog/schemas/{SCHEMA}/relations/orders"
        )
        assert order_detail.status_code == 200
        assert order_detail.json()["foreign_keys"][0]["target_relation"] == "customers"
        assert (
            analyst.get(
                f"/api/v1/connections/{connection_id}/catalog/schemas/{SCHEMA}/relations/order_amounts"
            ).status_code
            == 404
        )

    with TestClient(create_app(settings=TEST_SETTINGS)) as viewer:
        _login(
            viewer,
            email="scoped-viewer@example.com",
            password="connection visibility password",
        )
        assert viewer.get("/api/v1/connections").json()["data"] == []
        assert viewer.get(f"/api/v1/connections/{connection_id}").status_code == 404

    with TestClient(create_app(settings=TEST_SETTINGS)) as admin:
        _login(admin)
        removed = admin.delete(
            f"/api/v1/groups/{group_id}/members/{analyst_id}",
            headers=_headers(admin),
        )
        assert removed.status_code == 204

    with TestClient(create_app(settings=TEST_SETTINGS)) as analyst:
        _login(
            analyst,
            email="scoped-analyst@example.com",
            password="connection visibility password",
        )
        relations = analyst.get(
            f"/api/v1/connections/{connection_id}/catalog/schemas/{SCHEMA}/relations"
        )
        assert [item["name"] for item in relations.json()["data"]] == ["orders"]
        detail = analyst.get(
            f"/api/v1/connections/{connection_id}/catalog/schemas/{SCHEMA}/relations/orders"
        )
        assert detail.json()["foreign_keys"] == []

    with TestClient(create_app(settings=TEST_SETTINGS)) as admin:
        _login(admin)
        schema_grant = _grant(
            admin,
            connection_id,
            principal_type="user",
            principal_id=analyst_id,
            scope_type="schema",
            schema_name=SCHEMA,
        )

    with TestClient(create_app(settings=TEST_SETTINGS)) as analyst:
        _login(
            analyst,
            email="scoped-analyst@example.com",
            password="connection visibility password",
        )
        relations = analyst.get(
            f"/api/v1/connections/{connection_id}/catalog/schemas/{SCHEMA}/relations"
        )
        assert {item["name"] for item in relations.json()["data"]} == {
            "customers",
            "order_amounts",
            "orders",
        }

    with TestClient(create_app(settings=TEST_SETTINGS)) as admin:
        _login(admin)
        assert (
            admin.delete(
                f"/api/v1/connections/{connection_id}/grants/{schema_grant['id']}",
                headers=_headers(admin),
            ).status_code
            == 204
        )
        assert (
            admin.delete(
                f"/api/v1/connections/{connection_id}/grants/{orders_grant['id']}",
                headers=_headers(admin),
            ).status_code
            == 204
        )

    with TestClient(create_app(settings=TEST_SETTINGS)) as analyst:
        _login(
            analyst,
            email="scoped-analyst@example.com",
            password="connection visibility password",
        )
        assert analyst.get("/api/v1/connections").json()["data"] == []
        assert (
            analyst.get(f"/api/v1/connections/{connection_id}/catalog/schemas").status_code == 404
        )

    with TestClient(create_app(settings=TEST_SETTINGS)) as admin:
        _login(admin)
        all_grant = _grant(
            admin,
            connection_id,
            principal_type="user",
            principal_id=analyst_id,
            scope_type="all",
        )

    with TestClient(create_app(settings=TEST_SETTINGS)) as analyst:
        _login(
            analyst,
            email="scoped-analyst@example.com",
            password="connection visibility password",
        )
        relations = analyst.get(
            f"/api/v1/connections/{connection_id}/catalog/schemas/{SCHEMA}/relations"
        )
        assert {item["name"] for item in relations.json()["data"]} == {
            "customers",
            "order_amounts",
            "orders",
        }

    with TestClient(create_app(settings=TEST_SETTINGS)) as admin:
        _login(admin)
        assert (
            admin.delete(
                f"/api/v1/connections/{connection_id}/grants/{all_grant['id']}",
                headers=_headers(admin),
            ).status_code
            == 204
        )

    events = asyncio.run(_connection_grant_audit_events())
    assert [event.action for event in events].count("connection.grant_created") == 4
    assert [event.action for event in events].count("connection.grant_revoked") == 3
    assert all(event.resource_id == UUID(connection_id) for event in events)
    assert all(
        set(event.details)
        == {
            "grant_id",
            "principal_type",
            "principal_id",
            "scope_type",
            "schema_name",
            "relation_name",
        }
        for event in events
    )
    assert not any(
        forbidden in repr([event.details for event in events]).lower()
        for forbidden in ("password", "sql", "row")
    )
