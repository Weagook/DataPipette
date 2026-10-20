"""Administrator audit-event API integration tests."""

import asyncio
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from redis.asyncio import Redis
from sqlalchemy import text

from datapipette.api.dependencies.authentication import CSRF_COOKIE_NAME
from datapipette.application.auth_service import bootstrap_first_admin
from datapipette.config import Settings
from datapipette.domain.audit import AuditActorType, AuditOutcome
from datapipette.infrastructure.database import create_database_engine, create_session_factory
from datapipette.infrastructure.models import AuditEventModel
from datapipette.infrastructure.repositories.authentication import SqlAlchemyAuthenticationStore
from datapipette.main import create_app

TEST_SETTINGS = Settings(
    environment="test",
    docs_enabled=False,
    login_rate_limit_attempts=20,
    login_rate_limit_window_seconds=60,
)
ADMIN_EMAIL = "audit-admin@example.com"
ADMIN_PASSWORD = "correct horse battery staple"
VIEWER_PASSWORD = "viewer password for audit tests"


async def _reset_state() -> None:
    engine = create_database_engine(TEST_SETTINGS)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "TRUNCATE api_tokens, browser_sessions, audit_events, users "
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
            "Audit Admin",
            ADMIN_PASSWORD,
        )
    finally:
        await engine.dispose()


async def _seed_events(actor_id: UUID, resource_id: UUID) -> list[AuditEventModel]:
    now = datetime.now(UTC)
    events = [
        AuditEventModel(
            id=uuid4(),
            occurred_at=now - timedelta(seconds=offset),
            actor_type=(
                AuditActorType.SERVICE_ACCOUNT.value if offset == 0 else AuditActorType.USER.value
            ),
            actor_id=actor_id,
            action="test.audit_event",
            outcome=(AuditOutcome.FAILURE.value if offset == 1 else AuditOutcome.SUCCESS.value),
            resource_type="dataset",
            resource_id=resource_id,
            request_id=f"audit-request-{offset}",
            details={"offset": offset, "safe": True},
        )
        for offset in range(4)
    ]
    engine = create_database_engine(TEST_SETTINGS)
    try:
        async with create_session_factory(engine)() as session, session.begin():
            session.add_all(events)
        return events
    finally:
        await engine.dispose()


@pytest.fixture(autouse=True)
def clean_state() -> Generator[None, None, None]:
    asyncio.run(_reset_state())
    yield
    asyncio.run(_reset_state())


def _login(client: TestClient, email: str, password: str) -> None:
    response = client.post("/api/v1/auth/sessions", json={"email": email, "password": password})
    assert response.status_code == 201, response.text


def _csrf(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies[CSRF_COOKIE_NAME]}


def test_admin_filters_and_cursor_paginates_safe_audit_events() -> None:
    asyncio.run(_bootstrap())
    resource_id = uuid4()

    with TestClient(create_app(settings=TEST_SETTINGS)) as client:
        assert client.get("/api/v1/audit-events").status_code == 401
        _login(client, ADMIN_EMAIL, ADMIN_PASSWORD)

        viewer_response = client.post(
            "/api/v1/users",
            json={
                "email": "audit-viewer@example.com",
                "display_name": "Audit Viewer",
                "password": VIEWER_PASSWORD,
                "role": "viewer",
            },
            headers=_csrf(client),
        )
        assert viewer_response.status_code == 201, viewer_response.text
        viewer_id = UUID(viewer_response.json()["id"])

        account_response = client.post(
            "/api/v1/service-accounts",
            json={"name": "Audit consumer", "role": "viewer"},
            headers=_csrf(client),
        )
        assert account_response.status_code == 201, account_response.text
        account_id = account_response.json()["id"]
        token_response = client.post(
            f"/api/v1/service-accounts/{account_id}/tokens",
            json={"name": "Audit test token", "scopes": ["dashboards:read"]},
            headers=_csrf(client),
        )
        assert token_response.status_code == 201, token_response.text
        plaintext_token = token_response.json()["token"]

        seeded = asyncio.run(_seed_events(viewer_id, resource_id))
        first = client.get(
            "/api/v1/audit-events",
            params={"action": "test.audit_event", "limit": 2},
        )
        assert first.status_code == 200, first.text
        first_payload = first.json()
        assert [item["id"] for item in first_payload["data"]] == [
            str(seeded[0].id),
            str(seeded[1].id),
        ]
        assert first_payload["meta"]["next_cursor"] is not None

        second = client.get(
            "/api/v1/audit-events",
            params={
                "action": "test.audit_event",
                "limit": 2,
                "cursor": first_payload["meta"]["next_cursor"],
            },
        )
        assert second.status_code == 200, second.text
        assert [item["id"] for item in second.json()["data"]] == [
            str(seeded[2].id),
            str(seeded[3].id),
        ]
        assert second.json()["meta"]["next_cursor"] is None

        filter_cases: list[tuple[str, Any, UUID]] = [
            ("actor_type", "service_account", seeded[0].id),
            ("actor_id", str(viewer_id), seeded[0].id),
            ("outcome", "failure", seeded[1].id),
            ("resource_type", "dataset", seeded[0].id),
            ("resource_id", str(resource_id), seeded[0].id),
            ("request_id", "audit-request-2", seeded[2].id),
            ("occurred_from", seeded[1].occurred_at.isoformat(), seeded[0].id),
            ("occurred_to", seeded[2].occurred_at.isoformat(), seeded[2].id),
        ]
        for key, value, expected_first_id in filter_cases:
            response = client.get(
                "/api/v1/audit-events",
                params={"action": "test.audit_event", key: value},
            )
            assert response.status_code == 200, response.text
            assert response.json()["data"][0]["id"] == str(expected_first_id)

        token_audit = client.get(
            "/api/v1/audit-events",
            params={"action": "identity.api_token_created"},
        )
        assert token_audit.status_code == 200
        assert len(token_audit.json()["data"]) == 1
        assert plaintext_token not in token_audit.text

        invalid_cursor = client.get("/api/v1/audit-events", params={"cursor": "%%%"})
        assert invalid_cursor.status_code == 400
        assert invalid_cursor.json()["code"] == "invalid_cursor"
        invalid_window = client.get(
            "/api/v1/audit-events",
            params={
                "occurred_from": "2026-10-15T00:00:00Z",
                "occurred_to": "2026-10-14T00:00:00Z",
            },
        )
        assert invalid_window.status_code == 422
        assert invalid_window.json()["code"] == "audit_filter_invalid"
        naive_time = client.get(
            "/api/v1/audit-events",
            params={"occurred_from": "2026-10-14T00:00:00"},
        )
        assert naive_time.status_code == 422
        assert naive_time.json()["code"] == "audit_filter_invalid"

    with TestClient(create_app(settings=TEST_SETTINGS)) as viewer_client:
        _login(viewer_client, "audit-viewer@example.com", VIEWER_PASSWORD)
        forbidden = viewer_client.get("/api/v1/audit-events")
        assert forbidden.status_code == 403
        assert forbidden.json()["code"] == "administrator_required"


def test_audit_reads_do_not_create_recursive_audit_events() -> None:
    asyncio.run(_bootstrap())
    with TestClient(create_app(settings=TEST_SETTINGS)) as client:
        _login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
        before = cast(dict[str, Any], client.get("/api/v1/audit-events").json())
        after = cast(dict[str, Any], client.get("/api/v1/audit-events").json())

    assert [item["id"] for item in before["data"]] == [item["id"] for item in after["data"]]
