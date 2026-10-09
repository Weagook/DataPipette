"""PostgreSQL connection API integration tests."""

import asyncio
from collections.abc import Generator
from typing import cast
from uuid import uuid4

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
TEST_CA_CERTIFICATE = "-----BEGIN CERTIFICATE-----\nZmFrZQ==\n-----END CERTIFICATE-----"


async def _reset() -> None:
    engine = create_database_engine(TEST_SETTINGS)
    try:
        async with engine.begin() as connection:
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


async def _encrypted_ca(name: str) -> str | None:
    engine = create_database_engine(TEST_SETTINGS)
    try:
        async with create_session_factory(engine)() as session:
            return await session.scalar(
                select(PostgresConnectionModel.encrypted_ca_certificate).where(
                    PostgresConnectionModel.name == name
                )
            )
    finally:
        await engine.dispose()


@pytest.fixture(autouse=True)
def clean_state() -> Generator[None, None, None]:
    asyncio.run(_reset())
    yield
    asyncio.run(_reset())


def _client() -> TestClient:
    return TestClient(create_app(settings=TEST_SETTINGS))


def _login(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/sessions",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
    )
    assert response.status_code == 201


def _headers(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies[CSRF_COOKIE_NAME]}


def _create(client: TestClient, name: str = "Local PostgreSQL") -> dict[str, object]:
    response = client.post(
        "/api/v1/connections",
        json={
            "name": name,
            "host": "127.0.0.1",
            "port": 5432,
            "database_name": "datepipette",
            "username": "datepipette",
            "password": SOURCE_PASSWORD,
            "ssl_mode": "disable",
        },
        headers=_headers(client),
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, object], response.json())


def test_connection_crud_encrypts_secrets_and_tests_real_postgres() -> None:
    asyncio.run(_bootstrap())
    with _client() as client:
        _login(client)
        created = _create(client)
        assert created["has_password"] is True
        assert created["has_ca_certificate"] is False
        assert "password" not in created

        duplicate = client.post(
            "/api/v1/connections",
            json={
                "name": "Local PostgreSQL",
                "host": "localhost",
                "database_name": "datepipette",
                "username": "datepipette",
                "password": "different",
                "ssl_mode": "disable",
            },
            headers=_headers(client),
        )
        assert duplicate.status_code == 409

        listed = client.get("/api/v1/connections")
        assert listed.status_code == 200
        assert len(listed.json()["data"]) == 1
        read = client.get(f"/api/v1/connections/{created['id']}")
        assert read.status_code == 200

        tested = client.post(
            f"/api/v1/connections/{created['id']}/tests",
            headers=_headers(client),
        )
        assert tested.status_code == 200
        assert tested.json()["succeeded"] is True
        assert tested.json()["server_version"] >= 170000

        renamed = client.patch(
            f"/api/v1/connections/{created['id']}",
            json={"name": "Primary source"},
            headers=_headers(client),
        )
        assert renamed.status_code == 200
        assert renamed.json()["name"] == "Primary source"
        assert renamed.json()["last_tested_at"] is None

        with_ca = client.post(
            "/api/v1/connections",
            json={
                "name": "TLS source",
                "host": "db.example.com",
                "database_name": "analytics",
                "username": "reader",
                "password": "another-secret",
                "ssl_mode": "verify-full",
                "ca_certificate": TEST_CA_CERTIFICATE,
            },
            headers=_headers(client),
        )
        assert with_ca.status_code == 201
        assert with_ca.json()["has_ca_certificate"] is True
        assert "ca_certificate" not in with_ca.json()
        encrypted_ca = asyncio.run(_encrypted_ca("TLS source"))
        assert encrypted_ca is not None
        assert "BEGIN CERTIFICATE" not in encrypted_ca

        conflict = client.patch(
            f"/api/v1/connections/{with_ca.json()['id']}",
            json={"name": "Primary source"},
            headers=_headers(client),
        )
        assert conflict.status_code == 409

        cleared_ca = client.patch(
            f"/api/v1/connections/{with_ca.json()['id']}",
            json={"ssl_mode": "require", "ca_certificate": None},
            headers=_headers(client),
        )
        assert cleared_ca.status_code == 200
        assert cleared_ca.json()["has_ca_certificate"] is False

        invalid_cursor = client.get("/api/v1/connections", params={"cursor": "%%%"})
        assert invalid_cursor.status_code == 400

    async def inspect() -> tuple[str, list[AuditEventModel]]:
        engine = create_database_engine(TEST_SETTINGS)
        try:
            async with create_session_factory(engine)() as session:
                encrypted = await session.scalar(
                    select(PostgresConnectionModel.encrypted_password).where(
                        PostgresConnectionModel.name == "Primary source"
                    )
                )
                events = list((await session.scalars(select(AuditEventModel))).all())
                assert encrypted is not None
                return encrypted, events
        finally:
            await engine.dispose()

    encrypted, events = asyncio.run(inspect())
    assert encrypted.startswith("v1.")
    assert SOURCE_PASSWORD not in encrypted
    assert SOURCE_PASSWORD not in repr(events)
    assert {event.action for event in events} >= {
        "connection.created",
        "connection.tested",
        "connection.updated",
    }


def test_connection_failures_are_redacted_and_corruption_is_safe() -> None:
    asyncio.run(_bootstrap())
    with _client() as client:
        _login(client)
        created = _create(client)
        connection_id = created["id"]

        changed = client.patch(
            f"/api/v1/connections/{connection_id}",
            json={"password": "definitely-wrong-password"},
            headers=_headers(client),
        )
        assert changed.status_code == 200
        failed = client.post(
            f"/api/v1/connections/{connection_id}/tests",
            headers=_headers(client),
        )
        assert failed.status_code == 200
        assert failed.json() == {
            "succeeded": False,
            "tested_at": failed.json()["tested_at"],
            "error_code": "authentication_failed",
            "server_version": None,
        }
        assert "wrong-password" not in failed.text

    async def corrupt() -> None:
        engine = create_database_engine(TEST_SETTINGS)
        try:
            async with create_session_factory(engine)() as session, session.begin():
                await session.execute(
                    update(PostgresConnectionModel).values(encrypted_password="v1.corrupt")
                )
        finally:
            await engine.dispose()

    asyncio.run(corrupt())
    with _client() as client:
        _login(client)
        safe = client.post(
            f"/api/v1/connections/{connection_id}/tests",
            headers=_headers(client),
        )
        assert safe.status_code == 200
        assert safe.json()["error_code"] == "secret_unavailable"


def test_connection_api_requires_admin_csrf_and_valid_tls_configuration() -> None:
    asyncio.run(_bootstrap())
    with _client() as client:
        assert client.get("/api/v1/connections").status_code == 401
        _login(client)
        missing_csrf = client.post(
            "/api/v1/connections",
            json={
                "name": "No CSRF",
                "host": "localhost",
                "database_name": "db",
                "username": "user",
                "password": "secret",
                "ssl_mode": "disable",
            },
        )
        assert missing_csrf.status_code == 403

        missing_ca = client.post(
            "/api/v1/connections",
            json={
                "name": "TLS",
                "host": "db.example.com",
                "database_name": "db",
                "username": "user",
                "password": "secret",
                "ssl_mode": "verify-full",
            },
            headers=_headers(client),
        )
        assert missing_ca.status_code == 422
        assert missing_ca.json()["code"] == "connection_configuration_invalid"

        unknown = client.get(f"/api/v1/connections/{uuid4()}")
        assert unknown.status_code == 404
        unknown_id = uuid4()
        unknown_update = client.patch(
            f"/api/v1/connections/{unknown_id}",
            json={"name": "Missing"},
            headers=_headers(client),
        )
        assert unknown_update.status_code == 404
        unknown_test = client.post(
            f"/api/v1/connections/{unknown_id}/tests",
            headers=_headers(client),
        )
        assert unknown_test.status_code == 404

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

    with _client() as analyst_client:
        login = analyst_client.post(
            "/api/v1/auth/sessions",
            json={
                "email": "analyst@example.com",
                "password": "analyst password for tests",
            },
        )
        assert login.status_code == 201
        forbidden = analyst_client.get("/api/v1/connections")
        assert forbidden.status_code == 403
        assert forbidden.json()["code"] == "administrator_required"
