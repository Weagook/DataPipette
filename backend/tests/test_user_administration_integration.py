"""Administrator user-management behavior against PostgreSQL and Redis."""

import asyncio
from collections.abc import Generator
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from redis.asyncio import Redis
from sqlalchemy import select, text

from datepipette.api.dependencies.authentication import CSRF_COOKIE_NAME
from datepipette.application.auth_service import bootstrap_first_admin
from datepipette.config import Settings
from datepipette.infrastructure.database import create_database_engine, create_session_factory
from datepipette.infrastructure.models import AuditEventModel, UserModel
from datepipette.infrastructure.repositories.authentication import SqlAlchemyAuthenticationStore
from datepipette.main import create_app

TEST_SETTINGS = Settings(
    environment="test",
    docs_enabled=False,
    login_rate_limit_attempts=20,
    login_rate_limit_window_seconds=60,
)
ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "correct horse battery staple"
USER_PASSWORD = "analyst password for tests"


async def _reset_state() -> None:
    engine = create_database_engine(TEST_SETTINGS)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("TRUNCATE browser_sessions, audit_events, users RESTART IDENTITY CASCADE")
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
        store = SqlAlchemyAuthenticationStore(create_session_factory(engine))
        await bootstrap_first_admin(store, ADMIN_EMAIL, "Admin", ADMIN_PASSWORD)
    finally:
        await engine.dispose()


async def _audit_events(action: str) -> list[AuditEventModel]:
    engine = create_database_engine(TEST_SETTINGS)
    try:
        async with create_session_factory(engine)() as session:
            return list(
                (
                    await session.scalars(
                        select(AuditEventModel).where(AuditEventModel.action == action)
                    )
                ).all()
            )
    finally:
        await engine.dispose()


@pytest.fixture(autouse=True)
def clean_user_state() -> Generator[None, None, None]:
    asyncio.run(_reset_state())
    yield
    asyncio.run(_reset_state())


def _client() -> TestClient:
    return TestClient(create_app(settings=TEST_SETTINGS))


def _login(client: TestClient, email: str, password: str) -> None:
    response = client.post(
        "/api/v1/auth/sessions",
        json={"email": email, "password": password},
    )
    assert response.status_code == 201


def _csrf_headers(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies[CSRF_COOKIE_NAME]}


def _create_user(
    client: TestClient,
    *,
    email: str = "analyst@example.com",
    display_name: str = "Analyst",
    role: str = "analyst",
) -> dict[str, object]:
    response = client.post(
        "/api/v1/users",
        json={
            "email": email,
            "display_name": display_name,
            "password": USER_PASSWORD,
            "role": role,
        },
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, object], response.json())


def test_admin_creates_reads_and_cursor_paginates_users() -> None:
    asyncio.run(_bootstrap())
    raw_password = USER_PASSWORD

    with _client() as client:
        _login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
        analyst = _create_user(client)
        viewer = _create_user(
            client,
            email="viewer@example.com",
            display_name="Viewer",
            role="viewer",
        )

        duplicate = client.post(
            "/api/v1/users",
            json={
                "email": "ANALYST@example.com",
                "display_name": "Duplicate",
                "password": USER_PASSWORD,
                "role": "analyst",
            },
            headers=_csrf_headers(client),
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["code"] == "user_email_conflict"

        first_page = client.get("/api/v1/users", params={"limit": 2})
        assert first_page.status_code == 200
        assert len(first_page.json()["data"]) == 2
        cursor = first_page.json()["meta"]["next_cursor"]
        assert cursor is not None
        second_page = client.get(
            "/api/v1/users",
            params={"limit": 2, "cursor": cursor},
        )
        assert second_page.status_code == 200
        all_ids = {item["id"] for item in first_page.json()["data"] + second_page.json()["data"]}
        assert all_ids == {
            first_page.json()["data"][0]["id"],
            analyst["id"],
            viewer["id"],
        }
        assert second_page.json()["meta"]["next_cursor"] is None

        read = client.get(f"/api/v1/users/{analyst['id']}")
        assert read.status_code == 200
        assert read.json()["email"] == "analyst@example.com"
        assert "password" not in read.text

        invalid_cursor = client.get("/api/v1/users", params={"cursor": "%%%"})
        assert invalid_cursor.status_code == 400
        assert invalid_cursor.json()["code"] == "invalid_cursor"

    async def inspect_persistence() -> tuple[str, list[AuditEventModel]]:
        engine = create_database_engine(TEST_SETTINGS)
        try:
            async with create_session_factory(engine)() as session:
                password_hash = await session.scalar(
                    select(UserModel.password_hash).where(UserModel.email == "analyst@example.com")
                )
                events = list(
                    (
                        await session.scalars(
                            select(AuditEventModel).where(
                                AuditEventModel.action == "identity.user_created"
                            )
                        )
                    ).all()
                )
                assert password_hash is not None
                return password_hash, events
        finally:
            await engine.dispose()

    password_hash, events = asyncio.run(inspect_persistence())
    assert password_hash.startswith("$argon2id$")
    assert raw_password not in password_hash
    assert len(events) == 2
    assert raw_password not in repr(events)


def test_user_administration_requires_admin_and_csrf() -> None:
    asyncio.run(_bootstrap())
    with _client() as admin_client:
        assert admin_client.get("/api/v1/users").status_code == 401
        _login(admin_client, ADMIN_EMAIL, ADMIN_PASSWORD)

        missing_csrf = admin_client.post(
            "/api/v1/users",
            json={
                "email": "analyst@example.com",
                "display_name": "Analyst",
                "password": USER_PASSWORD,
                "role": "analyst",
            },
        )
        assert missing_csrf.status_code == 403
        assert missing_csrf.json()["code"] == "csrf_validation_failed"

        forged_csrf = admin_client.post(
            "/api/v1/users",
            json={
                "email": "analyst@example.com",
                "display_name": "Analyst",
                "password": USER_PASSWORD,
                "role": "analyst",
            },
            headers={"X-CSRF-Token": "forged"},
        )
        assert forged_csrf.status_code == 403

        invalid_name = admin_client.post(
            "/api/v1/users",
            json={
                "email": "invalid-name@example.com",
                "display_name": "   ",
                "password": USER_PASSWORD,
                "role": "analyst",
            },
            headers=_csrf_headers(admin_client),
        )
        assert invalid_name.status_code == 422
        assert USER_PASSWORD not in invalid_name.text
        _create_user(admin_client)

    with _client() as analyst_client:
        _login(analyst_client, "analyst@example.com", USER_PASSWORD)
        forbidden_read = analyst_client.get("/api/v1/users")
        assert forbidden_read.status_code == 403
        assert forbidden_read.json()["code"] == "administrator_required"

        forbidden_write = analyst_client.patch(
            f"/api/v1/users/{uuid4()}",
            json={"display_name": "Not allowed"},
            headers=_csrf_headers(analyst_client),
        )
        assert forbidden_write.status_code == 403
        assert forbidden_write.json()["code"] == "administrator_required"


def test_update_preserves_last_admin_and_disabling_revokes_sessions() -> None:
    asyncio.run(_bootstrap())
    with _client() as admin_client:
        _login(admin_client, ADMIN_EMAIL, ADMIN_PASSWORD)
        admin = admin_client.get("/api/v1/auth/me").json()
        analyst = _create_user(admin_client)

        last_admin = admin_client.patch(
            f"/api/v1/users/{admin['id']}",
            json={"role": "viewer"},
            headers=_csrf_headers(admin_client),
        )
        assert last_admin.status_code == 409
        assert last_admin.json()["code"] == "last_active_administrator"

        backup_admin = _create_user(
            admin_client,
            email="backup-admin@example.com",
            display_name="Backup admin",
            role="admin",
        )
        demoted_backup = admin_client.patch(
            f"/api/v1/users/{backup_admin['id']}",
            json={"role": "viewer"},
            headers=_csrf_headers(admin_client),
        )
        assert demoted_backup.status_code == 200
        assert demoted_backup.json()["role"] == "viewer"

        updated = admin_client.patch(
            f"/api/v1/users/{analyst['id']}",
            json={"display_name": "Updated analyst", "role": "viewer"},
            headers=_csrf_headers(admin_client),
        )
        assert updated.status_code == 200
        assert updated.json()["display_name"] == "Updated analyst"
        assert updated.json()["role"] == "viewer"

        null_patch = admin_client.patch(
            f"/api/v1/users/{analyst['id']}",
            json={"role": None},
            headers=_csrf_headers(admin_client),
        )
        assert null_patch.status_code == 422

    with _client() as user_client:
        _login(user_client, "analyst@example.com", USER_PASSWORD)
        with _client() as admin_client:
            _login(admin_client, ADMIN_EMAIL, ADMIN_PASSWORD)
            disabled = admin_client.patch(
                f"/api/v1/users/{analyst['id']}",
                json={"status": "disabled"},
                headers=_csrf_headers(admin_client),
            )
            assert disabled.status_code == 200
            assert disabled.json()["status"] == "disabled"
        assert user_client.get("/api/v1/auth/me").status_code == 401

    with _client() as admin_client:
        _login(admin_client, ADMIN_EMAIL, ADMIN_PASSWORD)
        reactivated = admin_client.patch(
            f"/api/v1/users/{analyst['id']}",
            json={"status": "active"},
            headers=_csrf_headers(admin_client),
        )
        assert reactivated.status_code == 200
        assert reactivated.json()["status"] == "active"

    with _client() as reactivated_user_client:
        _login(reactivated_user_client, "analyst@example.com", USER_PASSWORD)

    update_events = asyncio.run(_audit_events("identity.user_updated"))
    assert len(update_events) == 4
    assert all("changes" in event.details for event in update_events)
    assert USER_PASSWORD not in repr(update_events)


def test_admin_revokes_all_sessions_without_disabling_user() -> None:
    asyncio.run(_bootstrap())
    with _client() as admin_client:
        _login(admin_client, ADMIN_EMAIL, ADMIN_PASSWORD)
        analyst = _create_user(admin_client)

        with _client() as user_client:
            _login(user_client, "analyst@example.com", USER_PASSWORD)
            revoked = admin_client.delete(
                f"/api/v1/users/{analyst['id']}/sessions",
                headers=_csrf_headers(admin_client),
            )
            assert revoked.status_code == 200
            assert revoked.json() == {"revoked_count": 1}
            assert user_client.get("/api/v1/auth/me").status_code == 401

            already_revoked = admin_client.delete(
                f"/api/v1/users/{analyst['id']}/sessions",
                headers=_csrf_headers(admin_client),
            )
            assert already_revoked.status_code == 200
            assert already_revoked.json() == {"revoked_count": 0}

        with _client() as new_user_client:
            _login(new_user_client, "analyst@example.com", USER_PASSWORD)
            assert new_user_client.get("/api/v1/auth/me").status_code == 200

        missing = admin_client.delete(
            f"/api/v1/users/{uuid4()}/sessions",
            headers=_csrf_headers(admin_client),
        )
        assert missing.status_code == 404
        assert missing.json()["code"] == "user_not_found"

    revocation_events = asyncio.run(_audit_events("identity.user_sessions_revoked"))
    assert [event.details["revoked_count"] for event in revocation_events] == [1, 0]


def test_read_and_update_unknown_user_return_same_not_found_problem() -> None:
    asyncio.run(_bootstrap())
    missing_id = UUID("00000000-0000-0000-0000-000000000001")
    with _client() as client:
        _login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
        read = client.get(f"/api/v1/users/{missing_id}")
        changed = client.patch(
            f"/api/v1/users/{missing_id}",
            json={"display_name": "Missing"},
            headers=_csrf_headers(client),
        )
    assert read.status_code == changed.status_code == 404
    assert read.json()["code"] == changed.json()["code"] == "user_not_found"
