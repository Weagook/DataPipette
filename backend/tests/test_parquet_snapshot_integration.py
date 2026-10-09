"""End-to-end local Parquet snapshot publication tests."""

import asyncio
import hashlib
from collections.abc import Generator
from copy import deepcopy
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
from datepipette.application.auth_service import bootstrap_first_admin
from datepipette.application.refresh_runs import RefreshRunService
from datepipette.application.refresh_worker import RefreshWorkerService
from datepipette.config import Settings
from datepipette.domain.refresh import RefreshClaimOutcome
from datepipette.infrastructure.crypto import AesGcmSecretCipher
from datepipette.infrastructure.database import create_database_engine, create_session_factory
from datepipette.infrastructure.models import (
    AuditEventModel,
    DatasetSnapshotModel,
)
from datepipette.infrastructure.parquet_snapshots import LocalParquetSnapshotExecutor
from datepipette.infrastructure.repositories.authentication import SqlAlchemyAuthenticationStore
from datepipette.infrastructure.repositories.refresh_runs import SqlAlchemyRefreshRunStore
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


def test_refresh_streams_parquet_publishes_and_preserves_previous_snapshot(
    tmp_path: Path,
) -> None:
    asyncio.run(_bootstrap_source())
    engine = create_database_engine(TEST_SETTINGS)
    session_factory = create_session_factory(engine)
    store = SqlAlchemyRefreshRunStore(session_factory)
    dispatcher = RecordingDispatcher()
    refresh_service = RefreshRunService(store, dispatcher)
    executor = LocalParquetSnapshotExecutor(
        session_factory,
        AesGcmSecretCipher(TEST_SETTINGS.connection_encryption_key.get_secret_value()),
        tmp_path,
        connect_timeout_seconds=5,
        statement_timeout_seconds=30,
        batch_rows=1,
    )
    worker = RefreshWorkerService(store, executor)
    try:
        with TestClient(
            create_app(settings=TEST_SETTINGS, refresh_run_service=refresh_service)
        ) as client:
            _login(client)
            dataset_id, etag, original_definition = _create_and_validate(
                client,
                _connection(client),
            )
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
            assert asyncio.run(worker.execute(first_run_id)) is RefreshClaimOutcome.CLAIMED

            completed = client.get(f"{refresh_url}/{first_run_id}")
            assert completed.json()["state"] == "succeeded"
            first_snapshot_id = UUID(completed.json()["snapshot_id"])
            dataset = client.get(f"/api/v1/datasets/{dataset_id}").json()
            assert UUID(dataset["active_snapshot_id"]) == first_snapshot_id
            assert dataset["active_version_id"] is not None
            draft = client.get(f"/api/v1/datasets/{dataset_id}/draft")
            assert draft.json()["definition"] == original_definition
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
            assert table.column_names == [
                "record_id",
                "label",
                "amount",
                "active",
                "occurred_at",
                "payload",
                "raw",
            ]
            assert table["amount"].to_pylist() == [Decimal("12.30"), None]
            assert table["payload"].to_pylist() == ['{"tier":"gold"}', None]

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


def test_empty_source_publishes_typed_zero_row_parquet(tmp_path: Path) -> None:
    asyncio.run(_bootstrap_source())
    asyncio.run(_delete_source_rows())
    engine = create_database_engine(TEST_SETTINGS)
    session_factory = create_session_factory(engine)
    store = SqlAlchemyRefreshRunStore(session_factory)
    dispatcher = RecordingDispatcher()
    executor = LocalParquetSnapshotExecutor(
        session_factory,
        AesGcmSecretCipher(TEST_SETTINGS.connection_encryption_key.get_secret_value()),
        tmp_path,
        connect_timeout_seconds=5,
        statement_timeout_seconds=30,
        batch_rows=1_000,
    )
    try:
        with TestClient(
            create_app(
                settings=TEST_SETTINGS,
                refresh_run_service=RefreshRunService(store, dispatcher),
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
            assert parquet.schema_arrow.names == [
                "record_id",
                "label",
                "amount",
                "active",
                "occurred_at",
                "payload",
                "raw",
            ]
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
