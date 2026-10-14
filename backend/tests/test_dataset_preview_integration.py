"""Bounded dataset draft preview API integration tests."""

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
from datepipette.infrastructure.models import AuditEventModel
from datepipette.infrastructure.repositories.authentication import SqlAlchemyAuthenticationStore
from datepipette.main import create_app

TEST_SETTINGS = Settings(environment="test", docs_enabled=False)
ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "correct horse battery staple"
SOURCE_PASSWORD = "datepipette_dev_only"
SCHEMA = "preview_fixture"


async def _reset() -> None:
    engine = create_database_engine(TEST_SETTINGS)
    try:
        async with engine.begin() as connection:
            await connection.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
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


async def _bootstrap_source() -> None:
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
                    CREATE TABLE {SCHEMA}."Mixed Data" (
                        id bigint NOT NULL,
                        amount numeric(12,2),
                        happened_at timestamp with time zone,
                        business_day date,
                        payload jsonb,
                        raw bytea,
                        external_id uuid
                    )
                    """
                )
            )
            await connection.execute(
                text(
                    f"""
                    INSERT INTO {SCHEMA}."Mixed Data"
                        (id, amount, happened_at, business_day, payload, raw, external_id)
                    VALUES
                        (1, 12.30, '2026-10-09 08:00:00+00', '2026-10-09',
                         '{{"tags":["alpha"]}}', decode('00ff', 'hex'),
                         '00000000-0000-0000-0000-000000000001'),
                        (2, 99.95, '2026-10-10 09:30:00+00', '2026-10-10',
                         '{{"tags":["beta"]}}', decode('0102', 'hex'),
                         '00000000-0000-0000-0000-000000000002'),
                        (3, NULL, NULL, NULL, NULL, NULL, NULL)
                    """
                )
            )
            await connection.execute(
                text(
                    f"CREATE TABLE {SCHEMA}.large_cell AS "
                    "SELECT repeat('x', 1100000)::text AS value"
                )
            )
    finally:
        await engine.dispose()


async def _replace_validation_artifact_text(dataset_id: str) -> None:
    """Prove preview recompiles JSON instead of trusting persisted generated SQL."""

    engine = create_database_engine(TEST_SETTINGS)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE dataset_versions
                    SET validation = jsonb_set(
                        jsonb_set(validation, '{state}', '"valid_with_warnings"'::jsonb),
                        '{compiled_sql}',
                        to_jsonb('DELETE FROM preview_fixture."Mixed Data"'::text)
                    )
                    WHERE dataset_id = CAST(:dataset_id AS uuid)
                    """
                ),
                {"dataset_id": dataset_id},
            )
    finally:
        await engine.dispose()


@pytest.fixture(autouse=True)
def clean_state() -> Generator[None, None, None]:
    asyncio.run(_reset())
    yield
    asyncio.run(_reset())


def _login(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/sessions",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
    )
    assert response.status_code == 201


def _csrf(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies[CSRF_COOKIE_NAME]}


def _connection(client: TestClient) -> str:
    response = client.post(
        "/api/v1/connections",
        json={
            "name": "Preview source",
            "host": "127.0.0.1",
            "port": 5432,
            "database_name": "datepipette",
            "username": "datepipette",
            "password": SOURCE_PASSWORD,
            "ssl_mode": "disable",
        },
        headers=_csrf(client),
    )
    assert response.status_code == 201, response.text
    return cast(str, response.json()["id"])


def _definition(
    *,
    relation_name: str = "Mixed Data",
    columns: tuple[str, ...] = (
        "id",
        "amount",
        "happened_at",
        "business_day",
        "payload",
        "raw",
        "external_id",
    ),
) -> dict[str, Any]:
    relation_id = str(uuid4())
    return {
        "schema_version": 1,
        "source": {
            "relations": [
                {
                    "id": relation_id,
                    "schema": SCHEMA,
                    "name": relation_name,
                    "alias": "source_data",
                }
            ]
        },
        "fields": [
            {
                "id": str(uuid4()),
                "source": {"relation_id": relation_id, "column": column},
                "name": column,
                "display": {"label": column, "format": None, "visible": True},
            }
            for column in columns
        ],
        "joins": [],
    }


def _dataset(
    client: TestClient,
    connection_id: str,
    definition: dict[str, Any],
) -> tuple[str, str]:
    response = client.post(
        "/api/v1/datasets",
        json={
            "name": f"Preview {uuid4()}",
            "connection_id": connection_id,
            "definition": definition,
        },
        headers=_csrf(client),
    )
    assert response.status_code == 201, response.text
    return cast(str, response.json()["id"]), response.headers["etag"]


def _validate(client: TestClient, dataset_id: str, etag: str) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/datasets/{dataset_id}/draft/validations",
        headers={**_csrf(client), "If-Match": etag},
    )
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def test_preview_streams_bounded_json_safe_rows_and_audits_metadata_only() -> None:
    asyncio.run(_bootstrap_source())
    with TestClient(create_app(settings=TEST_SETTINGS)) as client:
        _login(client)
        dataset_id, etag = _dataset(client, _connection(client), _definition())
        validation = _validate(client, dataset_id, etag)
        asyncio.run(_replace_validation_artifact_text(dataset_id))

        current_policy = client.get("/api/v1/query-safety-policy")
        replacement = {
            key: value
            for key, value in current_policy.json().items()
            if key not in {"revision", "updated_at", "updated_by"}
        }
        replacement["preview_max_rows"] = 1
        replaced_policy = client.put(
            "/api/v1/query-safety-policy",
            json=replacement,
            headers={**_csrf(client), "If-Match": current_policy.headers["ETag"]},
        )
        assert replaced_policy.status_code == 200, replaced_policy.text

        policy_blocked = client.post(
            f"/api/v1/datasets/{dataset_id}/draft/previews",
            json={"limit": 2},
            headers={**_csrf(client), "If-Match": etag},
        )
        assert policy_blocked.status_code == 422
        assert policy_blocked.json()["code"] == "preview_policy_limit_exceeded"

        response = client.post(
            f"/api/v1/datasets/{dataset_id}/draft/previews",
            json={"limit": 1},
            headers={**_csrf(client), "If-Match": etag},
        )
        assert response.status_code == 200, response.text
        assert response.headers["etag"] == etag
        payload = response.json()
        assert payload["revision"] == 1
        assert payload["policy_revision"] == 1
        assert payload["columns"] == validation["result_schema"]
        assert payload["requested_limit"] == 1
        assert payload["returned_rows"] == 1
        assert payload["truncated"] is True
        assert payload["truncation_reason"] == "row_limit"

        rows = sorted(payload["rows"], key=lambda row: row["id"])
        assert rows[0] == {
            "id": 1,
            "amount": "12.30",
            "happened_at": "2026-10-09T08:00:00+00:00",
            "business_day": "2026-10-09",
            "payload": {"tags": ["alpha"]},
            "raw": "AP8=",
            "external_id": "00000000-0000-0000-0000-000000000001",
        }

        too_many = client.post(
            f"/api/v1/datasets/{dataset_id}/draft/previews",
            json={"limit": 201},
            headers={**_csrf(client), "If-Match": etag},
        )
        assert too_many.status_code == 422

    async def preview_events() -> list[AuditEventModel]:
        engine = create_database_engine(TEST_SETTINGS)
        try:
            async with create_session_factory(engine)() as session:
                return list(
                    (
                        await session.scalars(
                            select(AuditEventModel).where(
                                AuditEventModel.action == "dataset.draft_previewed"
                            )
                        )
                    ).all()
                )
        finally:
            await engine.dispose()

    events = asyncio.run(preview_events())
    assert len(events) == 1
    assert events[0].details == {
        "revision": 1,
        "requested_limit": 1,
        "returned_rows": 1,
        "truncation_reason": "row_limit",
    }
    assert "alpha" not in repr(events)
    assert "12.30" not in repr(events)


def test_preview_requires_current_successful_validation_and_csrf() -> None:
    asyncio.run(_bootstrap_source())
    definition = _definition()
    with TestClient(create_app(settings=TEST_SETTINGS)) as client:
        _login(client)
        connection_id = _connection(client)
        dataset_id, etag = _dataset(client, connection_id, definition)
        path = f"/api/v1/datasets/{dataset_id}/draft/previews"

        assert client.post(path, json={"limit": 1}, headers={"If-Match": etag}).status_code == 403
        assert client.post(path, json={"limit": 1}, headers=_csrf(client)).status_code == 428
        not_validated = client.post(
            path,
            json={"limit": 1},
            headers={**_csrf(client), "If-Match": etag},
        )
        assert not_validated.status_code == 409
        assert not_validated.json()["code"] == "dataset_draft_not_validated"

        _validate(client, dataset_id, etag)
        stale_revision = client.post(
            path,
            json={"limit": 1},
            headers={**_csrf(client), "If-Match": '"draft-2"'},
        )
        assert stale_revision.status_code == 409
        assert stale_revision.json()["code"] == "dataset_revision_conflict"

        changed = deepcopy(definition)
        changed["fields"][0]["display"]["label"] = "Identifier"
        replaced = client.put(
            f"/api/v1/datasets/{dataset_id}/draft",
            json={"definition": changed},
            headers={**_csrf(client), "If-Match": etag},
        )
        assert replaced.status_code == 200
        after_edit = client.post(
            path,
            json={"limit": 1},
            headers={**_csrf(client), "If-Match": replaced.headers["etag"]},
        )
        assert after_edit.status_code == 409
        assert after_edit.json()["code"] == "dataset_draft_not_validated"

        invalid_definition = _definition(columns=("missing_column",))
        invalid_id, invalid_etag = _dataset(client, connection_id, invalid_definition)
        invalid_validation = _validate(client, invalid_id, invalid_etag)
        assert invalid_validation["state"] == "invalid"
        invalid_preview = client.post(
            f"/api/v1/datasets/{invalid_id}/draft/previews",
            json={"limit": 1},
            headers={**_csrf(client), "If-Match": invalid_etag},
        )
        assert invalid_preview.status_code == 409
        assert invalid_preview.json()["code"] == "dataset_draft_not_validated"


def test_preview_enforces_byte_budget_and_redacts_source_failures() -> None:
    asyncio.run(_bootstrap_source())
    with TestClient(create_app(settings=TEST_SETTINGS)) as client:
        _login(client)
        connection_id = _connection(client)
        dataset_id, etag = _dataset(
            client,
            connection_id,
            _definition(relation_name="large_cell", columns=("value",)),
        )
        _validate(client, dataset_id, etag)

        byte_limited = client.post(
            f"/api/v1/datasets/{dataset_id}/draft/previews",
            json={"limit": 1},
            headers={**_csrf(client), "If-Match": etag},
        )
        assert byte_limited.status_code == 200
        assert byte_limited.json()["rows"] == []
        assert byte_limited.json()["returned_rows"] == 0
        assert byte_limited.json()["truncation_reason"] == "byte_limit"

        changed_connection = client.patch(
            f"/api/v1/connections/{connection_id}",
            json={"password": "wrong-preview-password"},
            headers=_csrf(client),
        )
        assert changed_connection.status_code == 200
        unavailable = client.post(
            f"/api/v1/datasets/{dataset_id}/draft/previews",
            json={"limit": 1},
            headers={**_csrf(client), "If-Match": etag},
        )
        assert unavailable.status_code == 502
        assert unavailable.json()["code"] == "source_preview_unavailable"
        assert "wrong-preview-password" not in unavailable.text

    async def failed_event() -> AuditEventModel:
        engine = create_database_engine(TEST_SETTINGS)
        try:
            async with create_session_factory(engine)() as session:
                event = await session.scalar(
                    select(AuditEventModel).where(
                        AuditEventModel.action == "dataset.draft_preview_failed"
                    )
                )
                assert event is not None
                return event
        finally:
            await engine.dispose()

    event = asyncio.run(failed_event())
    assert event.outcome == "failure"
    assert event.details == {"revision": 1, "error_code": "source_unavailable"}
