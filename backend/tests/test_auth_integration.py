"""Local authentication against real PostgreSQL and Redis adapters."""

import asyncio
import sys
from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response as HttpResponse
from redis.asyncio import Redis
from sqlalchemy import select, text, update

from datepipette import cli
from datepipette.api.dependencies.authentication import (
    CSRF_COOKIE_NAME,
    SESSION_COOKIE_NAME,
)
from datepipette.application.auth_service import bootstrap_first_admin
from datepipette.config import Settings
from datepipette.infrastructure.database import create_database_engine, create_session_factory
from datepipette.infrastructure.models import AuditEventModel, BrowserSessionModel, UserModel
from datepipette.infrastructure.repositories.authentication import SqlAlchemyAuthenticationStore
from datepipette.main import create_app

TEST_SETTINGS = Settings(
    environment="test",
    docs_enabled=False,
    login_rate_limit_attempts=3,
    login_rate_limit_window_seconds=60,
)
ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "correct horse battery staple"


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


@pytest.fixture(autouse=True)
def clean_auth_state() -> Generator[None, None, None]:
    asyncio.run(_reset_state())
    yield
    asyncio.run(_reset_state())


def _client() -> TestClient:
    return TestClient(create_app(settings=TEST_SETTINGS))


def _login(client: TestClient, password: str = ADMIN_PASSWORD) -> HttpResponse:
    return client.post(
        "/api/v1/auth/sessions",
        json={"email": ADMIN_EMAIL, "password": password},
        headers={"X-Request-ID": "auth-test"},
    )


def test_login_me_and_csrf_protected_logout() -> None:
    asyncio.run(_bootstrap())

    with _client() as client:
        login = _login(client)
        assert login.status_code == 201
        assert login.json()["user"] == {
            "id": login.json()["user"]["id"],
            "email": ADMIN_EMAIL,
            "display_name": "Admin",
            "role": "admin",
        }
        assert login.headers["Cache-Control"] == "no-store"
        set_cookie = login.headers.get_list("set-cookie")
        assert any("HttpOnly" in cookie and "SameSite=strict" in cookie for cookie in set_cookie)
        assert all("Secure" not in cookie for cookie in set_cookie)

        me = client.get("/api/v1/auth/me")
        assert me.status_code == 200
        assert me.json()["email"] == ADMIN_EMAIL

        missing_csrf = client.delete("/api/v1/auth/sessions/current")
        assert missing_csrf.status_code == 403
        assert missing_csrf.headers["content-type"].startswith("application/problem+json")
        assert missing_csrf.json()["code"] == "csrf_validation_failed"

        mismatched_csrf = client.delete(
            "/api/v1/auth/sessions/current",
            headers={"X-CSRF-Token": "forged-token"},
        )
        assert mismatched_csrf.status_code == 403

        csrf_token = client.cookies[CSRF_COOKIE_NAME]
        logout = client.delete(
            "/api/v1/auth/sessions/current",
            headers={"X-CSRF-Token": csrf_token},
        )
        assert logout.status_code == 204

        rejected = client.get("/api/v1/auth/me")
        assert rejected.status_code == 401
        assert rejected.json()["code"] == "authentication_required"


def test_unknown_and_wrong_credentials_share_safe_error() -> None:
    asyncio.run(_bootstrap())

    with _client() as client:
        wrong = _login(client, "not the password")
        unknown = client.post(
            "/api/v1/auth/sessions",
            json={"email": "unknown@example.com", "password": "not the password"},
        )

    assert wrong.status_code == 401
    assert unknown.status_code == 401
    assert wrong.json()["code"] == unknown.json()["code"] == "invalid_credentials"
    assert ADMIN_EMAIL not in str(wrong.json())
    assert "unknown@example.com" not in str(unknown.json())


def test_repeated_failures_are_throttled() -> None:
    asyncio.run(_bootstrap())

    with _client() as client:
        assert _login(client, "wrong password").status_code == 401
        assert _login(client, "wrong password").status_code == 401
        throttled = _login(client, "wrong password")
        still_throttled = _login(client, ADMIN_PASSWORD)

    assert throttled.status_code == 429
    assert int(throttled.headers["Retry-After"]) > 0
    assert throttled.json()["code"] == "login_rate_limited"
    assert still_throttled.status_code == 429


def test_disabled_and_expired_sessions_are_rejected() -> None:
    asyncio.run(_bootstrap())
    with _client() as client:
        assert _login(client).status_code == 201

        async def expire_session() -> None:
            engine = create_database_engine(TEST_SETTINGS)
            try:
                async with create_session_factory(engine)() as session, session.begin():
                    await session.execute(
                        update(BrowserSessionModel).values(
                            expires_at=datetime.now(UTC) - timedelta(seconds=1)
                        )
                    )
            finally:
                await engine.dispose()

        asyncio.run(expire_session())
        assert client.get("/api/v1/auth/me").status_code == 401

    async def disable_user() -> None:
        engine = create_database_engine(TEST_SETTINGS)
        try:
            async with create_session_factory(engine)() as session, session.begin():
                await session.execute(update(UserModel).values(status="disabled"))
        finally:
            await engine.dispose()

    asyncio.run(disable_user())
    with _client() as client:
        assert _login(client).status_code == 401


def test_database_contains_hashes_and_redacted_audit_only() -> None:
    asyncio.run(_bootstrap())
    with _client() as client:
        login = _login(client)
        assert login.status_code == 201
        session_cookie = client.cookies[SESSION_COOKIE_NAME]

    async def inspect_state() -> tuple[str, str, list[AuditEventModel]]:
        engine = create_database_engine(TEST_SETTINGS)
        try:
            async with create_session_factory(engine)() as session:
                password_hash = await session.scalar(select(UserModel.password_hash))
                token_hash = await session.scalar(select(BrowserSessionModel.token_hash))
                events = list((await session.scalars(select(AuditEventModel))).all())
                assert password_hash is not None
                assert token_hash is not None
                return password_hash, token_hash, events
        finally:
            await engine.dispose()

    password_hash, token_hash, events = asyncio.run(inspect_state())
    assert password_hash.startswith("$argon2id$")
    assert ADMIN_PASSWORD not in password_hash
    assert len(token_hash) == 64
    assert session_cookie not in token_hash
    assert {event.action for event in events} == {
        "identity.admin_bootstrapped",
        "auth.login",
    }
    assert ADMIN_PASSWORD not in repr(events)
    assert session_cookie not in repr(events)


def test_validation_problem_never_reflects_password() -> None:
    raw_password = "raw-password-that-must-not-be-reflected"
    with _client() as client:
        response = client.post(
            "/api/v1/auth/sessions",
            json={"email": "not-an-email", "password": raw_password},
        )

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "request_validation_failed"
    assert raw_password not in response.text


def test_bootstrap_cli_prompts_and_refuses_second_admin(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    passwords = iter([ADMIN_PASSWORD, ADMIN_PASSWORD])
    monkeypatch.setattr(cli, "getpass", lambda _prompt: next(passwords))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "datepipette-admin",
            "bootstrap-admin",
            "--email",
            ADMIN_EMAIL,
            "--display-name",
            "Admin",
        ],
    )

    cli.main()
    assert f"Created administrator {ADMIN_EMAIL}" in capsys.readouterr().out

    passwords = iter([ADMIN_PASSWORD, ADMIN_PASSWORD])
    monkeypatch.setattr(cli, "getpass", lambda _prompt: next(passwords))
    with pytest.raises(SystemExit, match="administrator already exists"):
        cli.main()
