"""Explicit live dataset draft validation integration tests."""

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
SCHEMA = "validation_fixture"


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
                    CREATE TABLE {SCHEMA}."Order Data" (
                        "ID" bigint NOT NULL,
                        "Total Amount" numeric(12,2),
                        created_at timestamp with time zone
                    )
                    """
                )
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


def _connection(client: TestClient, password: str = SOURCE_PASSWORD) -> str:
    response = client.post(
        "/api/v1/connections",
        json={
            "name": f"Source {uuid4()}",
            "host": "127.0.0.1",
            "port": 5432,
            "database_name": "datepipette",
            "username": "datepipette",
            "password": password,
            "ssl_mode": "disable",
        },
        headers=_csrf(client),
    )
    assert response.status_code == 201, response.text
    return cast(str, response.json()["id"])


def _definition(
    *, relation_name: str = "Order Data", missing_field: bool = False
) -> dict[str, Any]:
    relation_id = str(uuid4())
    fields = [
        {
            "id": str(uuid4()),
            "source": {"relation_id": relation_id, "column": "ID"},
            "name": "record_id",
            "display": {"label": "Record ID", "format": None, "visible": True},
        },
        {
            "id": str(uuid4()),
            "source": {
                "relation_id": relation_id,
                "column": "missing" if missing_field else "Total Amount",
            },
            "name": "total_amount",
            "display": {"label": "Total amount", "format": None, "visible": True},
        },
    ]
    return {
        "schema_version": 1,
        "source": {
            "relations": [
                {
                    "id": relation_id,
                    "schema": SCHEMA,
                    "name": relation_name,
                    "alias": "order_data",
                }
            ]
        },
        "fields": fields,
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
            "name": f"Dataset {uuid4()}",
            "connection_id": connection_id,
            "definition": definition,
        },
        headers=_csrf(client),
    )
    assert response.status_code == 201, response.text
    return cast(str, response.json()["id"]), response.headers["etag"]


def test_validation_compiles_quoted_select_persists_types_and_is_invalidated() -> None:
    asyncio.run(_bootstrap_source())
    definition = _definition()
    with TestClient(create_app(settings=TEST_SETTINGS)) as client:
        _login(client)
        connection_id = _connection(client)
        dataset_id, etag = _dataset(client, connection_id, definition)

        response = client.post(
            f"/api/v1/datasets/{dataset_id}/draft/validations",
            headers={**_csrf(client), "If-Match": etag},
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["state"] == "valid"
        assert result["validated_revision"] == 1
        assert result["compiled_sql"] == (
            'SELECT "order_data"."ID" AS "record_id", '
            '"order_data"."Total Amount" AS "total_amount" '
            f'FROM "{SCHEMA}"."Order Data" AS "order_data"'
        )
        assert result["result_schema"] == [
            {
                "field_id": definition["fields"][0]["id"],
                "name": "record_id",
                "data_type": "bigint",
                "nullable": False,
            },
            {
                "field_id": definition["fields"][1]["id"],
                "name": "total_amount",
                "data_type": "numeric(12,2)",
                "nullable": True,
            },
        ]
        assert result["plan"]["node_type"] == "Seq Scan"
        assert result["plan"]["estimated_rows"] >= 0
        assert result["plan"]["node_count"] == 1
        assert result["plan"]["max_sequential_scan_rows"] >= 0
        assert result["findings"] == []
        assert len(result["relation_statistics"]) == 1
        assert (
            result["relation_statistics"][0]["relation_id"]
            == (definition["source"]["relations"][0]["id"])
        )
        assert result["relation_statistics"][0]["schema_name"] == SCHEMA
        assert result["relation_statistics"][0]["relation_name"] == "Order Data"
        assert result["relation_statistics"][0]["visible_index_count"] == 0

        stored = client.get(f"/api/v1/datasets/{dataset_id}/draft")
        assert stored.status_code == 200
        assert stored.json()["validation"] == result

        changed_connection = client.patch(
            f"/api/v1/connections/{connection_id}",
            json={"password": "wrong-source-password"},
            headers=_csrf(client),
        )
        assert changed_connection.status_code == 200
        failed_retry = client.post(
            f"/api/v1/datasets/{dataset_id}/draft/validations",
            headers={**_csrf(client), "If-Match": etag},
        )
        assert failed_retry.status_code == 502
        retained = client.get(f"/api/v1/datasets/{dataset_id}/draft")
        assert retained.json()["validation"] == result

        changed = deepcopy(definition)
        changed["fields"][0]["display"]["label"] = "Identifier"
        replaced = client.put(
            f"/api/v1/datasets/{dataset_id}/draft",
            json={"definition": changed},
            headers={**_csrf(client), "If-Match": etag},
        )
        assert replaced.status_code == 200
        assert replaced.json()["validation"] is None

    async def audit_details() -> list[tuple[str, str, dict[str, Any]]]:
        engine = create_database_engine(TEST_SETTINGS)
        try:
            async with create_session_factory(engine)() as session:
                events = await session.scalars(
                    select(AuditEventModel)
                    .where(AuditEventModel.action.like("dataset.draft_validat%"))
                    .order_by(AuditEventModel.occurred_at)
                )
                return [(event.action, event.outcome, event.details) for event in events]
        finally:
            await engine.dispose()

    details = asyncio.run(audit_details())
    assert details == [
        (
            "dataset.draft_validated",
            "success",
            {"revision": 1, "state": "valid", "field_count": 2, "finding_codes": []},
        ),
        (
            "dataset.draft_validation_failed",
            "failure",
            {"revision": 1, "error_code": "source_unavailable"},
        ),
    ]
    assert "SELECT" not in repr(details)


def test_validation_returns_structured_findings_for_source_drift() -> None:
    asyncio.run(_bootstrap_source())
    with TestClient(create_app(settings=TEST_SETTINGS)) as client:
        assert client.get("/api/v1/query-safety-policy").status_code == 401
        _login(client)
        connection_id = _connection(client)

        missing_column_definition = _definition(missing_field=True)
        dataset_id, etag = _dataset(client, connection_id, missing_column_definition)
        missing_column = client.post(
            f"/api/v1/datasets/{dataset_id}/draft/validations",
            headers={**_csrf(client), "If-Match": etag},
        )
        assert missing_column.status_code == 200
        assert missing_column.json()["state"] == "invalid"
        assert missing_column.json()["compiled_sql"] is None
        assert missing_column.json()["plan"] is None
        assert missing_column.json()["findings"] == [
            {
                "code": "source_field_not_found",
                "severity": "error",
                "field_id": missing_column_definition["fields"][1]["id"],
                "relation_id": missing_column_definition["source"]["relations"][0]["id"],
                "join_id": None,
                "observed_value": None,
                "threshold_value": None,
            }
        ]

        missing_relation_definition = _definition(relation_name="Missing Relation")
        other_id, other_etag = _dataset(client, connection_id, missing_relation_definition)
        missing_relation = client.post(
            f"/api/v1/datasets/{other_id}/draft/validations",
            headers={**_csrf(client), "If-Match": other_etag},
        )
        assert missing_relation.status_code == 200
        assert missing_relation.json()["state"] == "invalid"
        assert missing_relation.json()["findings"] == [
            {
                "code": "source_relation_not_found",
                "severity": "error",
                "field_id": None,
                "relation_id": missing_relation_definition["source"]["relations"][0]["id"],
                "join_id": None,
                "observed_value": None,
                "threshold_value": None,
            }
        ]


def test_managed_query_safety_policy_blocks_and_admin_override_is_audited() -> None:
    asyncio.run(_bootstrap_source())
    with TestClient(create_app(settings=TEST_SETTINGS)) as client:
        _login(client)
        initial_policy = client.get("/api/v1/query-safety-policy")
        assert initial_policy.status_code == 200
        assert initial_policy.json()["revision"] == 0
        assert initial_policy.headers["ETag"] == '"query-safety-policy-0"'
        replacement = {
            **{
                key: value
                for key, value in initial_policy.json().items()
                if key not in {"revision", "updated_at", "updated_by"}
            },
            "total_cost_warning": 0.01,
            "total_cost_block": 0.02,
        }
        missing_csrf = client.put(
            "/api/v1/query-safety-policy",
            json=replacement,
            headers={"If-Match": initial_policy.headers["ETag"]},
        )
        assert missing_csrf.status_code == 403
        missing_revision = client.put(
            "/api/v1/query-safety-policy",
            json=replacement,
            headers=_csrf(client),
        )
        assert missing_revision.status_code == 428
        policy = client.put(
            "/api/v1/query-safety-policy",
            json=replacement,
            headers={**_csrf(client), "If-Match": initial_policy.headers["ETag"]},
        )
        assert policy.status_code == 200, policy.text
        assert policy.json()["revision"] == 1
        assert policy.headers["ETag"] == '"query-safety-policy-1"'
        stale_policy = client.put(
            "/api/v1/query-safety-policy",
            json=replacement,
            headers={**_csrf(client), "If-Match": '"query-safety-policy-0"'},
        )
        assert stale_policy.status_code == 409
        assert stale_policy.json()["code"] == "query_safety_policy_revision_conflict"
        second_policy = client.put(
            "/api/v1/query-safety-policy",
            json=replacement,
            headers={**_csrf(client), "If-Match": policy.headers["ETag"]},
        )
        assert second_policy.status_code == 200
        assert second_policy.json()["revision"] == 2

        definition = _definition()
        dataset_id, etag = _dataset(client, _connection(client), definition)

        validation = client.post(
            f"/api/v1/datasets/{dataset_id}/draft/validations",
            headers={**_csrf(client), "If-Match": etag},
        )

        assert validation.status_code == 200, validation.text
        assert validation.json()["state"] == "blocked"
        assert validation.json()["policy_revision"] == 2
        assert validation.json()["query_safety_override"] is None
        cost_finding = next(
            item for item in validation.json()["findings"] if item["code"] == "high_planner_cost"
        )
        assert cost_finding["severity"] == "blocked"
        assert cost_finding["observed_value"] >= cost_finding["threshold_value"]

        preview = client.post(
            f"/api/v1/datasets/{dataset_id}/draft/previews",
            json={"limit": 1},
            headers={**_csrf(client), "If-Match": etag},
        )
        assert preview.status_code == 409
        assert preview.json()["code"] == "dataset_draft_not_validated"

        overridden = client.post(
            f"/api/v1/datasets/{dataset_id}/draft/validations",
            json={"override_reason": "Approved for a bounded portfolio test execution."},
            headers={**_csrf(client), "If-Match": etag},
        )
        assert overridden.status_code == 200, overridden.text
        assert overridden.json()["state"] == "valid_with_warnings"
        assert overridden.json()["policy_revision"] == 2
        assert overridden.json()["query_safety_override"]["reason"] == (
            "Approved for a bounded portfolio test execution."
        )
        assert "high_planner_cost" in overridden.json()["query_safety_override"]["finding_codes"]
        assert all(item["severity"] != "blocked" for item in overridden.json()["findings"])

        preview_after_override = client.post(
            f"/api/v1/datasets/{dataset_id}/draft/previews",
            json={"limit": 1},
            headers={**_csrf(client), "If-Match": etag},
        )
        assert preview_after_override.status_code == 200, preview_after_override.text

        policy_audit = client.get(
            "/api/v1/audit-events",
            params={"action": "query_safety.policy_replaced"},
        )
        override_audit = client.get(
            "/api/v1/audit-events",
            params={"action": "query_safety.block_override_applied"},
        )
        assert len(policy_audit.json()["data"]) == 2
        assert len(override_audit.json()["data"]) == 1
        assert override_audit.json()["data"][0]["details"]["reason"] == (
            "Approved for a bounded portfolio test execution."
        )


def test_validation_requires_csrf_revision_and_redacts_source_failures() -> None:
    asyncio.run(_bootstrap_source())
    with TestClient(create_app(settings=TEST_SETTINGS)) as client:
        _login(client)
        dataset_id, etag = _dataset(
            client,
            _connection(client, password="wrong-source-password"),
            _definition(),
        )
        path = f"/api/v1/datasets/{dataset_id}/draft/validations"

        assert client.post(path, headers={"If-Match": etag}).status_code == 403
        missing_revision = client.post(path, headers=_csrf(client))
        assert missing_revision.status_code == 428
        stale = client.post(
            path,
            headers={**_csrf(client), "If-Match": '"draft-2"'},
        )
        assert stale.status_code == 409

        unavailable = client.post(
            path,
            headers={**_csrf(client), "If-Match": etag},
        )
        assert unavailable.status_code == 502
        assert unavailable.json()["code"] == "source_validation_unavailable"
        assert "wrong-source-password" not in unavailable.text
        assert client.get(f"/api/v1/datasets/{dataset_id}/draft").json()["validation"] is None
