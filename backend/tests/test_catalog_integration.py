"""Live PostgreSQL catalog API integration tests."""

import asyncio
from collections.abc import Generator
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from redis.asyncio import Redis
from sqlalchemy import text, update

from datepipette.api.dependencies.authentication import CSRF_COOKIE_NAME
from datepipette.application.auth_service import bootstrap_first_admin
from datepipette.config import Settings
from datepipette.infrastructure.database import create_database_engine, create_session_factory
from datepipette.infrastructure.models import PostgresConnectionModel
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


def test_catalog_returns_safe_errors_and_requires_an_administrator() -> None:
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
        forbidden = analyst_client.get(f"/api/v1/connections/{connection_id}/catalog/schemas")
        assert forbidden.status_code == 403
        assert forbidden.json()["code"] == "administrator_required"
