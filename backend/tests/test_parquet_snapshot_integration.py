"""End-to-end local Parquet snapshot publication tests."""

import asyncio
import hashlib
from collections.abc import Generator
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest
from fastapi.testclient import TestClient
from redis.asyncio import Redis
from sqlalchemy import select, text

from datepipette.api.dependencies.authentication import CSRF_COOKIE_NAME
from datepipette.application.access_control import DatasetAccessAuthorizer
from datepipette.application.auth_service import bootstrap_first_admin
from datepipette.application.connection_access import ConnectionAccessAuthorizer
from datepipette.application.refresh_runs import RefreshRunService
from datepipette.application.refresh_worker import RefreshExecutionResult, RefreshWorkerService
from datepipette.application.snapshot_queries import DatasetSnapshotQueryService
from datepipette.config import Settings
from datepipette.domain.refresh import RefreshClaimOutcome
from datepipette.infrastructure.crypto import AesGcmSecretCipher
from datepipette.infrastructure.database import create_database_engine, create_session_factory
from datepipette.infrastructure.duckdb_snapshot_queries import (
    DuckDbLocalSnapshotQueryExecutor,
)
from datepipette.infrastructure.models import (
    AuditEventModel,
    DatasetSnapshotModel,
)
from datepipette.infrastructure.parquet_snapshots import LocalParquetSnapshotExecutor
from datepipette.infrastructure.repositories.access_control import SqlAlchemyAccessControlStore
from datepipette.infrastructure.repositories.authentication import SqlAlchemyAuthenticationStore
from datepipette.infrastructure.repositories.connection_access import (
    SqlAlchemyConnectionAccessStore,
)
from datepipette.infrastructure.repositories.datasets import SqlAlchemyDatasetStore
from datepipette.infrastructure.repositories.refresh_runs import SqlAlchemyRefreshRunStore
from datepipette.infrastructure.repositories.snapshot_queries import (
    SqlAlchemySnapshotQueryStore,
)
from datepipette.infrastructure.snapshot_cursor import EncryptedSnapshotCursorCodec
from datepipette.main import create_app

TEST_SETTINGS = Settings(environment="test", docs_enabled=False)
ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "correct horse battery staple"
SOURCE_PASSWORD = "datepipette_dev_only"
SCHEMA = "snapshot_fixture"


class RecordingDispatcher:
    def __init__(self) -> None:
        self.run_ids: list[UUID] = []

    def dispatch(self, run_id: UUID) -> None:
        self.run_ids.append(run_id)


async def _reset() -> None:
    engine = create_database_engine(TEST_SETTINGS)
    try:
        async with engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE'))
            await connection.execute(
                text(
                    "TRUNCATE dataset_snapshots, dataset_refresh_requests, "
                    "dataset_refresh_runs, dataset_versions, datasets, "
                    "postgresql_connections, browser_sessions, audit_events, users "
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
            await connection.execute(text(f'CREATE SCHEMA "{SCHEMA}"'))
            await connection.execute(
                text(
                    f'CREATE TABLE "{SCHEMA}"."records" ('
                    "id bigint NOT NULL, label text NOT NULL, amount numeric(12,2), "
                    "active boolean NOT NULL, occurred_at timestamptz NOT NULL, "
                    "payload jsonb, raw bytea, tags integer[])"
                )
            )
            await connection.execute(
                text(
                    f'CREATE TABLE "{SCHEMA}"."customers" ('
                    "id bigint PRIMARY KEY, name text NOT NULL)"
                )
            )
            await connection.execute(
                text(
                    f'CREATE TABLE "{SCHEMA}"."orders" ('
                    "id bigint PRIMARY KEY, customer_id bigint NOT NULL "
                    f'REFERENCES "{SCHEMA}"."customers"(id), amount numeric(12,2) NOT NULL)'
                )
            )
            await connection.execute(
                text(
                    f'INSERT INTO "{SCHEMA}"."customers" (id, name) VALUES '
                    "(1, 'Ada'), (2, 'Grace')"
                )
            )
            await connection.execute(
                text(
                    f'INSERT INTO "{SCHEMA}"."orders" (id, customer_id, amount) VALUES '
                    "(10, 1, 42.50)"
                )
            )
            await connection.execute(
                text(
                    f'INSERT INTO "{SCHEMA}"."records" '
                    "(id, label, amount, active, occurred_at, payload, raw, tags) VALUES "
                    "(1, 'Alpha private value', 12.30, true, "
                    "'2026-01-02T03:04:05Z', '{\"tier\":\"gold\"}', "
                    "decode('00ff', 'hex'), ARRAY[1,2]), "
                    "(2, 'Beta private value', NULL, false, "
                    "'2026-02-03T04:05:06Z', NULL, NULL, ARRAY[3])"
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


def _connection(client: TestClient) -> str:
    response = client.post(
        "/api/v1/connections",
        json={
            "name": "Snapshot source",
            "host": "localhost",
            "port": 5432,
            "database_name": "datepipette",
            "username": "datepipette",
            "password": SOURCE_PASSWORD,
            "ssl_mode": "disable",
        },
        headers=_csrf(client),
    )
    assert response.status_code == 201
    return cast(str, response.json()["id"])


def _definition(*, include_tags: bool = False) -> dict[str, Any]:
    relation_id = str(uuid4())
    columns = [
        ("id", "record_id"),
        ("label", "label"),
        ("amount", "amount"),
        ("active", "active"),
        ("occurred_at", "occurred_at"),
        ("payload", "payload"),
        ("raw", "raw"),
    ]
    if include_tags:
        columns.append(("tags", "tags"))
    return {
        "schema_version": 1,
        "source": {
            "relations": [
                {
                    "id": relation_id,
                    "schema": SCHEMA,
                    "name": "records",
                    "alias": "records",
                }
            ]
        },
        "fields": [
            {
                "id": str(uuid4()),
                "source": {"relation_id": relation_id, "column": source},
                "name": output,
                "display": {
                    "label": output.replace("_", " ").title(),
                    "format": None,
                    "visible": True,
                },
            }
            for source, output in columns
        ],
        "joins": [],
    }


def _joined_definition(*, missing_join_column: bool = False) -> dict[str, Any]:
    customers_id = str(uuid4())
    orders_id = str(uuid4())
    return {
        "schema_version": 2,
        "source": {
            "relations": [
                {
                    "id": customers_id,
                    "schema": SCHEMA,
                    "name": "customers",
                    "alias": "customer",
                },
                {
                    "id": orders_id,
                    "schema": SCHEMA,
                    "name": "orders",
                    "alias": "customer_order",
                },
            ]
        },
        "fields": [
            {
                "id": str(uuid4()),
                "source": {"relation_id": customers_id, "column": "id"},
                "name": "customer_id",
                "display": {"label": "Customer ID", "format": None, "visible": True},
            },
            {
                "id": str(uuid4()),
                "source": {"relation_id": customers_id, "column": "name"},
                "name": "customer_name",
                "display": {"label": "Customer", "format": None, "visible": True},
            },
            {
                "id": str(uuid4()),
                "source": {"relation_id": orders_id, "column": "amount"},
                "name": "order_amount",
                "display": {"label": "Amount", "format": None, "visible": True},
            },
        ],
        "joins": [
            {
                "id": str(uuid4()),
                "type": "left",
                "left_relation_id": customers_id,
                "right_relation_id": orders_id,
                "conditions": [
                    {
                        "left_column": "id",
                        "right_column": "missing" if missing_join_column else "customer_id",
                    }
                ],
            }
        ],
    }


def _create_and_validate(
    client: TestClient,
    connection_id: str,
) -> tuple[str, str, dict[str, Any]]:
    definition = _definition()
    created = client.post(
        "/api/v1/datasets",
        json={
            "name": "Snapshot records",
            "connection_id": connection_id,
            "definition": definition,
        },
        headers=_csrf(client),
    )
    assert created.status_code == 201, created.text
    dataset_id = cast(str, created.json()["id"])
    etag = created.headers["etag"]
    validated = client.post(
        f"/api/v1/datasets/{dataset_id}/draft/validations",
        headers={**_csrf(client), "If-Match": etag},
    )
    assert validated.status_code == 200, validated.text
    assert validated.json()["state"] == "valid"
    return dataset_id, etag, definition


def _snapshot_query_service(
    session_factory: Any,
    storage_root: Path,
    maximum_bytes: int = 2_097_152,
) -> DatasetSnapshotQueryService:
    return DatasetSnapshotQueryService(
        SqlAlchemySnapshotQueryStore(session_factory),
        DuckDbLocalSnapshotQueryExecutor(
            storage_root,
            timeout_seconds=10,
            memory_limit_mb=128,
            threads=2,
            temporary_directory_limit_mb=128,
            maximum_concurrent_queries=2,
        ),
        EncryptedSnapshotCursorCodec(TEST_SETTINGS.connection_encryption_key.get_secret_value()),
        maximum_bytes,
        DatasetAccessAuthorizer(SqlAlchemyAccessControlStore(session_factory)),
    )


def test_refresh_streams_parquet_publishes_and_preserves_previous_snapshot(
    tmp_path: Path,
) -> None:
    asyncio.run(_bootstrap_source())
    engine = create_database_engine(TEST_SETTINGS)
    session_factory = create_session_factory(engine)
    store = SqlAlchemyRefreshRunStore(session_factory)
    dispatcher = RecordingDispatcher()
    connection_authorizer = ConnectionAccessAuthorizer(
        SqlAlchemyConnectionAccessStore(session_factory)
    )
    refresh_service = RefreshRunService(
        store,
        dispatcher,
        SqlAlchemyDatasetStore(session_factory),
        connection_authorizer,
    )
    executor = LocalParquetSnapshotExecutor(
        session_factory,
        AesGcmSecretCipher(TEST_SETTINGS.connection_encryption_key.get_secret_value()),
        tmp_path,
        connect_timeout_seconds=5,
        statement_timeout_seconds=30,
        batch_rows=1,
        authorizer=connection_authorizer,
    )
    worker = RefreshWorkerService(store, executor)
    try:
        with TestClient(
            create_app(
                settings=TEST_SETTINGS,
                refresh_run_service=refresh_service,
                snapshot_query_service=_snapshot_query_service(session_factory, tmp_path),
            )
        ) as client:
            _login(client)
            dataset_id, etag, original_definition = _create_and_validate(
                client,
                _connection(client),
            )
            freshness_url = f"/api/v1/datasets/{dataset_id}/freshness"
            unpublished_freshness = client.get(freshness_url)
            assert unpublished_freshness.status_code == 200
            assert unpublished_freshness.json() == {
                "dataset_id": str(dataset_id),
                "data_state": "not_published",
                "snapshot_id": None,
                "snapshot_row_count": None,
                "last_success_at": None,
                "refresh_state": "idle",
                "active_refresh_run_id": None,
                "refresh_requested_at": None,
                "refresh_started_at": None,
                "last_refresh_error": None,
                "last_refresh_error_at": None,
                "is_stale": False,
            }
            unpublished = client.post(
                f"/api/v1/datasets/{dataset_id}/rows:query",
                json={"fields": [original_definition["fields"][0]["id"]]},
            )
            assert unpublished.status_code == 409
            assert unpublished.json()["code"] == "dataset_snapshot_not_published"
            refresh_url = f"/api/v1/datasets/{dataset_id}/refresh-runs"
            requested = client.post(
                refresh_url,
                headers={
                    **_csrf(client),
                    "If-Match": etag,
                    "Idempotency-Key": "snapshot-one",
                },
            )
            assert requested.status_code == 202
            first_run_id = UUID(requested.json()["id"])
            queued_freshness = client.get(freshness_url).json()
            assert queued_freshness["data_state"] == "refreshing"
            assert queued_freshness["refresh_state"] == "queued"
            assert UUID(queued_freshness["active_refresh_run_id"]) == first_run_id
            assert queued_freshness["refresh_requested_at"] is not None
            assert queued_freshness["refresh_started_at"] is None
            assert asyncio.run(worker.execute(first_run_id)) is RefreshClaimOutcome.CLAIMED

            completed = client.get(f"{refresh_url}/{first_run_id}")
            assert completed.json()["state"] == "succeeded"
            first_snapshot_id = UUID(completed.json()["snapshot_id"])
            ready_freshness = client.get(freshness_url).json()
            assert ready_freshness["data_state"] == "ready"
            assert UUID(ready_freshness["snapshot_id"]) == first_snapshot_id
            assert ready_freshness["snapshot_row_count"] == 2
            assert ready_freshness["last_success_at"] is not None
            assert ready_freshness["refresh_state"] == "idle"
            assert ready_freshness["active_refresh_run_id"] is None
            dataset = client.get(f"/api/v1/datasets/{dataset_id}").json()
            assert UUID(dataset["active_snapshot_id"]) == first_snapshot_id
            assert dataset["active_version_id"] is not None
            draft = client.get(f"/api/v1/datasets/{dataset_id}/draft")
            assert draft.json()["definition"] == {"kind": "visual", **original_definition}
            assert draft.json()["validation"]["state"] == "valid"

            first_snapshot = asyncio.run(_snapshot(first_snapshot_id))
            first_path = tmp_path / first_snapshot.object_key
            assert first_path.is_file()
            assert first_snapshot.row_count == 2
            assert first_snapshot.byte_size == first_path.stat().st_size
            assert (
                first_snapshot.checksum_sha256
                == hashlib.sha256(first_path.read_bytes()).hexdigest()
            )
            table = pq.read_table(first_path)
            assert table.num_rows == 2
            assert pq.ParquetFile(first_path).num_row_groups == 2
            assert first_snapshot.schema_document["schema_version"] == 2
            expected_names = [
                "record_id",
                "label",
                "amount",
                "active",
                "occurred_at",
                "payload",
                "raw",
            ]
            fields = first_snapshot.schema_document["fields"]
            assert [field["name"] for field in fields] == expected_names
            assert table.column_names == [field["storage_name"] for field in fields]
            assert all(name.startswith("field_") for name in table.column_names)
            by_name = {field["name"]: field["storage_name"] for field in fields}
            assert table[by_name["amount"]].to_pylist() == [Decimal("12.30"), None]
            assert table[by_name["payload"]].to_pylist() == ['{"tier":"gold"}', None]

            field_ids = {field["name"]: field["id"] for field in original_definition["fields"]}
            query_url = f"/api/v1/datasets/{dataset_id}/rows:query"
            first_page = client.post(
                query_url,
                json={
                    "fields": [field_ids["record_id"], field_ids["label"]],
                    "sorts": [{"field_id": field_ids["active"], "direction": "asc"}],
                    "page_size": 1,
                },
            )
            assert first_page.status_code == 200, first_page.text
            assert first_page.json()["data"]["rows"] == [
                {"record_id": 2, "label": "Beta private value"}
            ]
            first_snapshot_cursor = first_page.json()["meta"]["next_cursor"]
            assert first_snapshot_cursor
            second_page = client.post(
                query_url,
                json={
                    "fields": [field_ids["record_id"], field_ids["label"]],
                    "sorts": [{"field_id": field_ids["active"], "direction": "asc"}],
                    "page_size": 1,
                    "cursor": first_snapshot_cursor,
                },
            )
            assert second_page.status_code == 200, second_page.text
            assert second_page.json()["data"]["rows"][0]["record_id"] == 1
            assert second_page.json()["meta"]["next_cursor"] is None

            asyncio.run(_insert_source_row())
            second = client.post(
                refresh_url,
                headers={
                    **_csrf(client),
                    "If-Match": draft.headers["etag"],
                    "Idempotency-Key": "snapshot-two",
                },
            )
            second_run_id = UUID(second.json()["id"])
            assert asyncio.run(worker.execute(second_run_id)) is RefreshClaimOutcome.CLAIMED
            second_result = client.get(f"{refresh_url}/{second_run_id}").json()
            assert second_result["state"] == "succeeded"
            second_snapshot_id = UUID(second_result["snapshot_id"])
            assert second_snapshot_id != first_snapshot_id
            assert first_path.exists()
            first_after, second_after = asyncio.run(
                _snapshots(first_snapshot_id, second_snapshot_id)
            )
            assert first_after.state == "superseded"
            assert second_after.state == "active"
            assert second_after.row_count == 3

            active_cursor = None
            paged_ids: list[int] = []
            first_active_cursor = None
            for _ in range(3):
                page = client.post(
                    query_url,
                    json={
                        "fields": [field_ids["record_id"]],
                        "sorts": [{"field_id": field_ids["active"], "direction": "asc"}],
                        "page_size": 1,
                        "cursor": active_cursor,
                    },
                )
                assert page.status_code == 200, page.text
                paged_ids.append(page.json()["data"]["rows"][0]["record_id"])
                active_cursor = page.json()["meta"]["next_cursor"]
                if first_active_cursor is None:
                    first_active_cursor = active_cursor
            assert paged_ids == [2, 1, 3]
            assert active_cursor is None
            mismatched = client.post(
                query_url,
                json={
                    "fields": [field_ids["label"]],
                    "sorts": [{"field_id": field_ids["active"], "direction": "asc"}],
                    "page_size": 1,
                    "cursor": first_active_cursor,
                },
            )
            assert mismatched.status_code == 400
            assert mismatched.json()["code"] == "invalid_snapshot_cursor"

            filtered = client.post(
                query_url,
                json={
                    "fields": [
                        field_ids["record_id"],
                        field_ids["amount"],
                        field_ids["occurred_at"],
                        field_ids["payload"],
                    ],
                    "filters": [
                        {
                            "field_id": field_ids["label"],
                            "operator": "in",
                            "values": ["Alpha private value", "Gamma private value"],
                        },
                        {
                            "field_id": field_ids["amount"],
                            "operator": "gte",
                            "value": "12.30",
                        },
                        {
                            "field_id": field_ids["occurred_at"],
                            "operator": "between",
                            "values": ["2026-01-01T00:00:00Z", "2026-12-31T23:59:59Z"],
                        },
                        {
                            "field_id": field_ids["label"],
                            "operator": "contains",
                            "value": "private",
                        },
                    ],
                    "sorts": [{"field_id": field_ids["amount"], "direction": "desc"}],
                    "page_size": 10,
                },
            )
            assert filtered.status_code == 200, filtered.text
            assert [row["record_id"] for row in filtered.json()["data"]["rows"]] == [3, 1]
            assert filtered.json()["data"]["rows"][0]["amount"] == "99.99"
            assert filtered.json()["data"]["rows"][0]["payload"] == {}
            assert filtered.json()["meta"]["snapshot_id"] == str(second_snapshot_id)
            assert filtered.json()["meta"]["snapshot_row_count"] == 3

            nulls = client.post(
                query_url,
                json={
                    "fields": [field_ids["record_id"]],
                    "filters": [{"field_id": field_ids["amount"], "operator": "is_null"}],
                    "page_size": 10,
                },
            )
            assert nulls.status_code == 200
            assert nulls.json()["data"]["rows"] == [{"record_id": 2}]

            stale_cursor = client.post(
                query_url,
                json={
                    "fields": [field_ids["record_id"], field_ids["label"]],
                    "sorts": [{"field_id": field_ids["active"], "direction": "asc"}],
                    "page_size": 1,
                    "cursor": first_snapshot_cursor,
                },
            )
            assert stale_cursor.status_code == 400
            assert stale_cursor.json()["code"] == "invalid_snapshot_cursor"

            invalid_operator = client.post(
                query_url,
                json={
                    "fields": [field_ids["record_id"]],
                    "filters": [
                        {
                            "field_id": field_ids["active"],
                            "operator": "contains",
                            "value": "true",
                        }
                    ],
                },
            )
            assert invalid_operator.status_code == 422
            assert invalid_operator.json()["code"] == "snapshot_query_invalid"

            latest_draft = client.get(f"/api/v1/datasets/{dataset_id}/draft")
            unsupported = deepcopy(latest_draft.json()["definition"])
            unsupported_definition = _definition(include_tags=True)
            unsupported["fields"] = unsupported_definition["fields"]
            unsupported["source"] = unsupported_definition["source"]
            replaced = client.put(
                f"/api/v1/datasets/{dataset_id}/draft",
                json={"definition": unsupported},
                headers={**_csrf(client), "If-Match": latest_draft.headers["etag"]},
            )
            assert replaced.status_code == 200
            unsupported_etag = replaced.headers["etag"]
            validation = client.post(
                f"/api/v1/datasets/{dataset_id}/draft/validations",
                headers={**_csrf(client), "If-Match": unsupported_etag},
            )
            assert validation.status_code == 200
            unsupported_run = client.post(
                refresh_url,
                headers={
                    **_csrf(client),
                    "If-Match": unsupported_etag,
                    "Idempotency-Key": "unsupported-array",
                },
            ).json()
            assert (
                asyncio.run(worker.execute(UUID(unsupported_run["id"])))
                is RefreshClaimOutcome.CLAIMED
            )
            failed = client.get(f"{refresh_url}/{unsupported_run['id']}").json()
            assert failed["state"] == "failed"
            assert failed["error_code"] == "unsupported_source_type"
            unchanged = client.get(f"/api/v1/datasets/{dataset_id}").json()
            assert UUID(unchanged["active_snapshot_id"]) == second_snapshot_id
            stale_rows = client.post(
                query_url,
                json={"fields": [field_ids["record_id"]], "page_size": 1},
            )
            assert stale_rows.status_code == 200
            assert stale_rows.json()["meta"]["snapshot_id"] == str(second_snapshot_id)
            assert stale_rows.json()["meta"]["is_stale"] is True
            assert stale_rows.json()["meta"]["last_refresh_error"] == "unsupported_source_type"
            assert stale_rows.json()["meta"]["last_refresh_error_at"] is not None
            assert stale_rows.json()["meta"]["refresh_state"] == "idle"
            stale_freshness = client.get(freshness_url).json()
            assert stale_freshness["data_state"] == "stale"
            assert stale_freshness["is_stale"] is True
            assert UUID(stale_freshness["snapshot_id"]) == second_snapshot_id
            assert stale_freshness["last_refresh_error"] == "unsupported_source_type"
            assert stale_freshness["last_refresh_error_at"] is not None

            retry = client.post(
                refresh_url,
                headers={
                    **_csrf(client),
                    "If-Match": unsupported_etag,
                    "Idempotency-Key": "unsupported-array-retry",
                },
            )
            assert retry.status_code == 202
            retrying_freshness = client.get(freshness_url).json()
            assert retrying_freshness["data_state"] == "refreshing"
            assert retrying_freshness["refresh_state"] == "queued"
            assert retrying_freshness["is_stale"] is True
            assert retrying_freshness["last_refresh_error"] == "unsupported_source_type"
    finally:
        asyncio.run(engine.dispose())

    async def inspect_audit() -> list[AuditEventModel]:
        audit_engine = create_database_engine(TEST_SETTINGS)
        try:
            async with create_session_factory(audit_engine)() as session:
                return list((await session.scalars(select(AuditEventModel))).all())
        finally:
            await audit_engine.dispose()

    audit = asyncio.run(inspect_audit())
    assert "Alpha private value" not in repr(audit)
    assert str(tmp_path) not in repr(audit)


def test_freshness_exposes_running_and_failed_first_publication(tmp_path: Path) -> None:
    asyncio.run(_bootstrap_source())
    engine = create_database_engine(TEST_SETTINGS)
    session_factory = create_session_factory(engine)
    store = SqlAlchemyRefreshRunStore(session_factory)
    refresh_service = RefreshRunService(
        store,
        RecordingDispatcher(),
        SqlAlchemyDatasetStore(session_factory),
        ConnectionAccessAuthorizer(SqlAlchemyConnectionAccessStore(session_factory)),
    )
    try:
        with TestClient(
            create_app(
                settings=TEST_SETTINGS,
                refresh_run_service=refresh_service,
                snapshot_query_service=_snapshot_query_service(session_factory, tmp_path),
            )
        ) as client:
            _login(client)
            dataset_id, etag, _ = _create_and_validate(client, _connection(client))
            requested = client.post(
                f"/api/v1/datasets/{dataset_id}/refresh-runs",
                headers={
                    **_csrf(client),
                    "If-Match": etag,
                    "Idempotency-Key": "failed-first-publication",
                },
            )
            assert requested.status_code == 202
            run_id = UUID(requested.json()["id"])
            outcome, _ = asyncio.run(store.claim(run_id, datetime.now(UTC)))
            assert outcome is RefreshClaimOutcome.CLAIMED

            running = client.get(f"/api/v1/datasets/{dataset_id}/freshness")
            assert running.status_code == 200
            assert running.json()["data_state"] == "refreshing"
            assert running.json()["refresh_state"] == "running"
            assert running.json()["active_refresh_run_id"] == str(run_id)
            assert running.json()["refresh_started_at"] is not None
            assert running.json()["snapshot_id"] is None

            asyncio.run(
                store.complete(
                    run_id,
                    RefreshExecutionResult(False, "source_unavailable"),
                    datetime.now(UTC),
                )
            )
            failed = client.get(f"/api/v1/datasets/{dataset_id}/freshness")
            assert failed.status_code == 200
            assert failed.json()["data_state"] == "failed"
            assert failed.json()["refresh_state"] == "idle"
            assert failed.json()["snapshot_id"] is None
            assert failed.json()["last_success_at"] is None
            assert failed.json()["last_refresh_error"] == "source_unavailable"
            assert failed.json()["last_refresh_error_at"] is not None
            assert failed.json()["is_stale"] is False
    finally:
        asyncio.run(engine.dispose())


def test_joined_definition_validates_previews_and_publishes_left_join_snapshot(
    tmp_path: Path,
) -> None:
    asyncio.run(_bootstrap_source())
    engine = create_database_engine(TEST_SETTINGS)
    session_factory = create_session_factory(engine)
    store = SqlAlchemyRefreshRunStore(session_factory)
    dispatcher = RecordingDispatcher()
    connection_authorizer = ConnectionAccessAuthorizer(
        SqlAlchemyConnectionAccessStore(session_factory)
    )
    refresh_service = RefreshRunService(
        store,
        dispatcher,
        SqlAlchemyDatasetStore(session_factory),
        connection_authorizer,
    )
    worker = RefreshWorkerService(
        store,
        LocalParquetSnapshotExecutor(
            session_factory,
            AesGcmSecretCipher(TEST_SETTINGS.connection_encryption_key.get_secret_value()),
            tmp_path,
            connect_timeout_seconds=5,
            statement_timeout_seconds=30,
            batch_rows=1,
            authorizer=connection_authorizer,
        ),
    )
    try:
        with TestClient(
            create_app(settings=TEST_SETTINGS, refresh_run_service=refresh_service)
        ) as client:
            _login(client)
            connection_id = _connection(client)
            definition = _joined_definition()
            created = client.post(
                "/api/v1/datasets",
                json={
                    "name": "Customer orders",
                    "connection_id": connection_id,
                    "definition": definition,
                },
                headers=_csrf(client),
            )
            assert created.status_code == 201, created.text
            dataset_id = cast(str, created.json()["id"])
            etag = created.headers["etag"]

            validated = client.post(
                f"/api/v1/datasets/{dataset_id}/draft/validations",
                headers={**_csrf(client), "If-Match": etag},
            )
            assert validated.status_code == 200, validated.text
            assert validated.json()["state"] == "valid"
            assert validated.json()["compiled_sql"] == (
                'SELECT "customer"."id" AS "customer_id", '
                '"customer"."name" AS "customer_name", '
                '"customer_order"."amount" AS "order_amount" '
                f'FROM "{SCHEMA}"."customers" AS "customer" '
                f'LEFT JOIN "{SCHEMA}"."orders" AS "customer_order" '
                'ON "customer"."id" = "customer_order"."customer_id"'
            )
            assert validated.json()["result_schema"][2]["nullable"] is True

            preview = client.post(
                f"/api/v1/datasets/{dataset_id}/draft/previews",
                json={"limit": 10},
                headers={**_csrf(client), "If-Match": etag},
            )
            assert preview.status_code == 200, preview.text
            assert sorted(preview.json()["rows"], key=lambda row: row["customer_id"]) == [
                {"customer_id": 1, "customer_name": "Ada", "order_amount": "42.50"},
                {"customer_id": 2, "customer_name": "Grace", "order_amount": None},
            ]

            requested = client.post(
                f"/api/v1/datasets/{dataset_id}/refresh-runs",
                headers={
                    **_csrf(client),
                    "If-Match": etag,
                    "Idempotency-Key": "joined-snapshot",
                },
            )
            assert requested.status_code == 202, requested.text
            run_id = UUID(requested.json()["id"])
            assert asyncio.run(worker.execute(run_id)) is RefreshClaimOutcome.CLAIMED
            completed = client.get(f"/api/v1/datasets/{dataset_id}/refresh-runs/{run_id}").json()
            assert completed["state"] == "succeeded"
            snapshot = asyncio.run(_snapshot(UUID(completed["snapshot_id"])))
            assert snapshot.row_count == 2
            table = pq.read_table(tmp_path / snapshot.object_key)
            by_name = {
                field["name"]: field["storage_name"] for field in snapshot.schema_document["fields"]
            }
            assert table[by_name["customer_id"]].to_pylist() == [1, 2]
            assert table[by_name["order_amount"]].to_pylist() == [Decimal("42.50"), None]

            invalid_definition = _joined_definition(missing_join_column=True)
            invalid = client.post(
                "/api/v1/datasets",
                json={
                    "name": "Invalid customer orders",
                    "connection_id": connection_id,
                    "definition": invalid_definition,
                },
                headers=_csrf(client),
            )
            invalid_result = client.post(
                f"/api/v1/datasets/{invalid.json()['id']}/draft/validations",
                headers={**_csrf(client), "If-Match": invalid.headers["etag"]},
            )
            assert invalid_result.status_code == 200
            assert invalid_result.json()["state"] == "invalid"
            assert invalid_result.json()["findings"] == [
                {
                    "code": "source_join_column_not_found",
                    "severity": "error",
                    "field_id": None,
                    "relation_id": invalid_definition["source"]["relations"][1]["id"],
                    "join_id": invalid_definition["joins"][0]["id"],
                    "observed_value": None,
                    "threshold_value": None,
                }
            ]
    finally:
        asyncio.run(engine.dispose())


def test_advanced_sql_definition_validates_previews_publishes_and_queries_snapshot(
    tmp_path: Path,
) -> None:
    asyncio.run(_bootstrap_source())
    engine = create_database_engine(TEST_SETTINGS)
    session_factory = create_session_factory(engine)
    store = SqlAlchemyRefreshRunStore(session_factory)
    dispatcher = RecordingDispatcher()
    connection_authorizer = ConnectionAccessAuthorizer(
        SqlAlchemyConnectionAccessStore(session_factory)
    )
    refresh_service = RefreshRunService(
        store,
        dispatcher,
        SqlAlchemyDatasetStore(session_factory),
        connection_authorizer,
    )
    worker = RefreshWorkerService(
        store,
        LocalParquetSnapshotExecutor(
            session_factory,
            AesGcmSecretCipher(TEST_SETTINGS.connection_encryption_key.get_secret_value()),
            tmp_path,
            connect_timeout_seconds=5,
            statement_timeout_seconds=30,
            batch_rows=1,
            authorizer=connection_authorizer,
        ),
    )
    field_ids = {name: str(uuid4()) for name in ("record_id", "label", "amount")}
    definition: dict[str, Any] = {
        "kind": "sql",
        "schema_version": 1,
        "statement": (
            f'WITH active_records AS (SELECT id, label, amount FROM "{SCHEMA}".records '
            "WHERE active) SELECT id AS record_id, label AS label, amount AS amount "
            "FROM active_records"
        ),
        "fields": [
            {
                "id": field_ids[name],
                "name": name,
                "display": {"label": name.title(), "format": None, "visible": True},
            }
            for name in ("record_id", "label", "amount")
        ],
    }
    try:
        with TestClient(
            create_app(
                settings=TEST_SETTINGS,
                refresh_run_service=refresh_service,
                snapshot_query_service=_snapshot_query_service(session_factory, tmp_path),
            )
        ) as client:
            _login(client)
            connection_id = _connection(client)
            unsafe = client.post(
                "/api/v1/datasets",
                json={
                    "name": "Unsafe SQL",
                    "connection_id": connection_id,
                    "definition": {**definition, "statement": f'DELETE FROM "{SCHEMA}".records'},
                },
                headers=_csrf(client),
            )
            assert unsafe.status_code == 422
            assert unsafe.json()["code"] == "dataset_definition_invalid"

            created = client.post(
                "/api/v1/datasets",
                json={
                    "name": "Active records SQL",
                    "connection_id": connection_id,
                    "definition": definition,
                },
                headers=_csrf(client),
            )
            assert created.status_code == 201, created.text
            dataset_id = cast(str, created.json()["id"])
            etag = created.headers["etag"]
            stored = client.get(f"/api/v1/datasets/{dataset_id}/draft")
            assert stored.status_code == 200
            stored_definition = stored.json()["definition"]
            assert stored_definition == definition

            validated = client.post(
                f"/api/v1/datasets/{dataset_id}/draft/validations",
                headers={**_csrf(client), "If-Match": etag},
            )
            assert validated.status_code == 200, validated.text
            validation = validated.json()
            assert validation["state"] == "valid"
            assert [field["name"] for field in validation["result_schema"]] == [
                "record_id",
                "label",
                "amount",
            ]
            assert validation["compiled_sql"] == definition["statement"]
            assert all(field["nullable"] for field in validation["result_schema"])

            preview = client.post(
                f"/api/v1/datasets/{dataset_id}/draft/previews",
                json={"limit": 10},
                headers={**_csrf(client), "If-Match": etag},
            )
            assert preview.status_code == 200, preview.text
            assert preview.json()["rows"] == [
                {"record_id": 1, "label": "Alpha private value", "amount": "12.30"}
            ]

            requested = client.post(
                f"/api/v1/datasets/{dataset_id}/refresh-runs",
                headers={
                    **_csrf(client),
                    "If-Match": etag,
                    "Idempotency-Key": "sql-snapshot",
                },
            )
            assert requested.status_code == 202, requested.text
            run_id = UUID(requested.json()["id"])
            assert asyncio.run(worker.execute(run_id)) is RefreshClaimOutcome.CLAIMED
            completed = client.get(f"/api/v1/datasets/{dataset_id}/refresh-runs/{run_id}").json()
            assert completed["state"] == "succeeded"

            rows = client.post(
                f"/api/v1/datasets/{dataset_id}/rows:query",
                json={
                    "fields": [field_ids["record_id"], field_ids["label"]],
                    "filters": [
                        {
                            "field_id": field_ids["record_id"],
                            "operator": "gte",
                            "value": 1,
                        }
                    ],
                },
            )
            assert rows.status_code == 200, rows.text
            assert rows.json()["data"]["rows"] == [{"record_id": 1, "label": "Alpha private value"}]

            mismatch = {**definition, "fields": [*definition["fields"]]}
            mismatch["fields"][0] = {**mismatch["fields"][0], "name": "wrong_name"}
            replaced = client.put(
                f"/api/v1/datasets/{dataset_id}/draft",
                json={"definition": mismatch},
                headers={**_csrf(client), "If-Match": etag},
            )
            mismatch_validation = client.post(
                f"/api/v1/datasets/{dataset_id}/draft/validations",
                headers={**_csrf(client), "If-Match": replaced.headers["etag"]},
            )
            assert mismatch_validation.status_code == 200
            assert mismatch_validation.json()["state"] == "invalid"
            assert mismatch_validation.json()["findings"][0]["code"] == (
                "sql_result_schema_mismatch"
            )
    finally:
        asyncio.run(engine.dispose())


def test_empty_source_publishes_typed_zero_row_parquet(tmp_path: Path) -> None:
    asyncio.run(_bootstrap_source())
    asyncio.run(_delete_source_rows())
    engine = create_database_engine(TEST_SETTINGS)
    session_factory = create_session_factory(engine)
    store = SqlAlchemyRefreshRunStore(session_factory)
    dispatcher = RecordingDispatcher()
    connection_authorizer = ConnectionAccessAuthorizer(
        SqlAlchemyConnectionAccessStore(session_factory)
    )
    executor = LocalParquetSnapshotExecutor(
        session_factory,
        AesGcmSecretCipher(TEST_SETTINGS.connection_encryption_key.get_secret_value()),
        tmp_path,
        connect_timeout_seconds=5,
        statement_timeout_seconds=30,
        batch_rows=1_000,
        authorizer=connection_authorizer,
    )
    try:
        with TestClient(
            create_app(
                settings=TEST_SETTINGS,
                refresh_run_service=RefreshRunService(
                    store,
                    dispatcher,
                    SqlAlchemyDatasetStore(session_factory),
                    connection_authorizer,
                ),
            )
        ) as client:
            _login(client)
            dataset_id, etag, _ = _create_and_validate(client, _connection(client))
            refresh_url = f"/api/v1/datasets/{dataset_id}/refresh-runs"
            requested = client.post(
                refresh_url,
                headers={
                    **_csrf(client),
                    "If-Match": etag,
                    "Idempotency-Key": "empty-snapshot",
                },
            ).json()
            worker = RefreshWorkerService(store, executor)
            assert asyncio.run(worker.execute(UUID(requested["id"]))) is RefreshClaimOutcome.CLAIMED
            completed = client.get(f"{refresh_url}/{requested['id']}").json()
            assert completed["state"] == "succeeded"
            snapshot = asyncio.run(_snapshot(UUID(completed["snapshot_id"])))
            parquet = pq.ParquetFile(tmp_path / snapshot.object_key)
            assert snapshot.row_count == 0
            assert parquet.metadata is not None
            assert parquet.metadata.num_rows == 0
            fields = snapshot.schema_document["fields"]
            assert [field["name"] for field in fields] == [
                "record_id",
                "label",
                "amount",
                "active",
                "occurred_at",
                "payload",
                "raw",
            ]
            assert parquet.schema_arrow.names == [field["storage_name"] for field in fields]
    finally:
        asyncio.run(engine.dispose())


async def _snapshot(snapshot_id: UUID) -> DatasetSnapshotModel:
    engine = create_database_engine(TEST_SETTINGS)
    try:
        async with create_session_factory(engine)() as session:
            snapshot = await session.get(DatasetSnapshotModel, snapshot_id)
            assert snapshot is not None
            return snapshot
    finally:
        await engine.dispose()


async def _snapshots(
    first_id: UUID,
    second_id: UUID,
) -> tuple[DatasetSnapshotModel, DatasetSnapshotModel]:
    return await _snapshot(first_id), await _snapshot(second_id)


async def _insert_source_row() -> None:
    engine = create_database_engine(TEST_SETTINGS)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    f'INSERT INTO "{SCHEMA}"."records" '
                    "(id, label, amount, active, occurred_at, payload, raw, tags) VALUES "
                    "(3, 'Gamma private value', 99.99, true, "
                    "'2026-03-04T05:06:07Z', '{}', NULL, ARRAY[4])"
                )
            )
    finally:
        await engine.dispose()


async def _delete_source_rows() -> None:
    engine = create_database_engine(TEST_SETTINGS)
    try:
        async with engine.begin() as connection:
            await connection.execute(text(f'DELETE FROM "{SCHEMA}"."records"'))
    finally:
        await engine.dispose()
