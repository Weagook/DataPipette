"""Durable refresh request and worker-state integration tests."""

import asyncio
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from redis.asyncio import Redis
from sqlalchemy import select, text

from datepipette.api.dependencies.authentication import CSRF_COOKIE_NAME
from datepipette.application.auth_service import bootstrap_first_admin
from datepipette.application.connection_access import ConnectionAccessAuthorizer
from datepipette.application.refresh_runs import RefreshRunService
from datepipette.application.refresh_worker import (
    RefreshExecutionResult,
    RefreshWorkerService,
    UnavailableSnapshotWriter,
)
from datepipette.config import Settings
from datepipette.domain.refresh import RefreshClaimOutcome, RefreshRun, RefreshRunState
from datepipette.domain.snapshots import SnapshotArtifact
from datepipette.infrastructure.celery_app import create_celery_app
from datepipette.infrastructure.database import create_database_engine, create_session_factory
from datepipette.infrastructure.models import (
    AuditEventModel,
    DatasetRefreshRequestModel,
    DatasetRefreshRunModel,
    DatasetVersionModel,
)
from datepipette.infrastructure.refresh_dispatch import CeleryRefreshDispatcher
from datepipette.infrastructure.repositories.authentication import SqlAlchemyAuthenticationStore
from datepipette.infrastructure.repositories.connection_access import (
    SqlAlchemyConnectionAccessStore,
)
from datepipette.infrastructure.repositories.datasets import SqlAlchemyDatasetStore
from datepipette.infrastructure.repositories.refresh_runs import SqlAlchemyRefreshRunStore
from datepipette.main import create_app

TEST_SETTINGS = Settings(environment="test", docs_enabled=False)
ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "correct horse battery staple"


class RecordingDispatcher:
    def __init__(self) -> None:
        self.run_ids: list[UUID] = []
        self.fail = False

    def dispatch(self, run_id: UUID) -> None:
        if self.fail:
            raise RuntimeError("broker unavailable")
        self.run_ids.append(run_id)


class SuccessfulExecutor:
    async def execute(self, run: RefreshRun) -> RefreshExecutionResult:
        snapshot_id = uuid4()
        return RefreshExecutionResult(
            succeeded=True,
            snapshot=SnapshotArtifact(
                id=snapshot_id,
                storage_backend="local",
                object_key=f"datasets/{run.dataset_id}/snapshots/{snapshot_id}.parquet",
                row_count=1,
                byte_size=100,
                checksum_sha256="0" * 64,
                schema_document={"schema_version": 1, "fields": []},
            ),
        )


class FailingExecutor:
    async def execute(self, _run: RefreshRun) -> RefreshExecutionResult:
        raise RuntimeError("sensitive infrastructure detail")


async def _reset() -> None:
    engine = create_database_engine(TEST_SETTINGS)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "TRUNCATE dataset_refresh_requests, dataset_refresh_runs, "
                    "dataset_versions, datasets, postgresql_connections, browser_sessions, "
                    "audit_events, users RESTART IDENTITY CASCADE"
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


def _definition() -> dict[str, Any]:
    relation_id = str(uuid4())
    return {
        "schema_version": 1,
        "source": {
            "relations": [
                {
                    "id": relation_id,
                    "schema": "public",
                    "name": "customers",
                    "alias": "customers",
                }
            ]
        },
        "fields": [
            {
                "id": str(uuid4()),
                "source": {"relation_id": relation_id, "column": "id"},
                "name": "customer_id",
                "display": {"label": "Customer ID", "format": None, "visible": True},
            }
        ],
        "joins": [],
    }


def _create_dataset(client: TestClient) -> tuple[str, str, dict[str, Any]]:
    connection = client.post(
        "/api/v1/connections",
        json={
            "name": "Offline source",
            "host": "source.invalid",
            "port": 5432,
            "database_name": "analytics",
            "username": "readonly",
            "password": "source-secret",
            "ssl_mode": "disable",
        },
        headers=_csrf(client),
    )
    definition = _definition()
    dataset = client.post(
        "/api/v1/datasets",
        json={
            "name": "Customers",
            "connection_id": connection.json()["id"],
            "definition": definition,
        },
        headers=_csrf(client),
    )
    assert dataset.status_code == 201
    return cast(str, dataset.json()["id"]), dataset.headers["etag"], definition


async def _mark_valid(dataset_id: str, definition: dict[str, Any]) -> None:
    field_id = definition["fields"][0]["id"]
    engine = create_database_engine(TEST_SETTINGS)
    try:
        async with create_session_factory(engine)() as session, session.begin():
            draft = await session.scalar(
                select(DatasetVersionModel).where(
                    DatasetVersionModel.dataset_id == UUID(dataset_id)
                )
            )
            assert draft is not None
            draft.validation = {
                "state": "valid",
                "validated_revision": draft.revision,
                "validated_at": datetime.now(UTC).isoformat(),
                "compiled_sql": 'SELECT "customers"."id" AS "customer_id"',
                "result_schema": [
                    {
                        "field_id": field_id,
                        "name": "customer_id",
                        "data_type": "bigint",
                        "nullable": False,
                    }
                ],
                "plan": None,
                "findings": [],
            }
    finally:
        await engine.dispose()


def test_refresh_requests_are_idempotent_coalesced_and_retryable() -> None:
    asyncio.run(_bootstrap())
    engine = create_database_engine(TEST_SETTINGS)
    session_factory = create_session_factory(engine)
    store = SqlAlchemyRefreshRunStore(session_factory)
    dispatcher = RecordingDispatcher()
    service = RefreshRunService(
        store,
        dispatcher,
        SqlAlchemyDatasetStore(session_factory),
        ConnectionAccessAuthorizer(SqlAlchemyConnectionAccessStore(session_factory)),
    )
    try:
        with TestClient(create_app(settings=TEST_SETTINGS, refresh_run_service=service)) as client:
            _login(client)
            dataset_id, etag, definition = _create_dataset(client)
            url = f"/api/v1/datasets/{dataset_id}/refresh-runs"

            unvalidated = client.post(
                url,
                headers={**_csrf(client), "If-Match": etag, "Idempotency-Key": "first"},
            )
            assert unvalidated.status_code == 409
            assert unvalidated.json()["code"] == "dataset_draft_not_validated"
            asyncio.run(_mark_valid(dataset_id, definition))

            missing_key = client.post(url, headers={**_csrf(client), "If-Match": etag})
            assert missing_key.status_code == 428

            first = client.post(
                url,
                headers={**_csrf(client), "If-Match": etag, "Idempotency-Key": "first"},
            )
            assert first.status_code == 202, first.text
            assert first.json()["state"] == "queued"
            assert first.headers["x-datepipette-coalesced"] == "false"
            run_id = first.json()["id"]
            assert dispatcher.run_ids == [UUID(run_id)]

            repeated = client.post(
                url,
                headers={**_csrf(client), "If-Match": etag, "Idempotency-Key": "first"},
            )
            coalesced = client.post(
                url,
                headers={**_csrf(client), "If-Match": etag, "Idempotency-Key": "second"},
            )
            assert repeated.json()["id"] == run_id
            assert coalesced.json()["id"] == run_id
            assert repeated.headers["x-datepipette-coalesced"] == "true"
            assert coalesced.headers["x-datepipette-coalesced"] == "true"
            assert dispatcher.run_ids == [UUID(run_id)]
            assert client.get(f"{url}/{run_id}").status_code == 200

            worker = RefreshWorkerService(store, UnavailableSnapshotWriter())
            assert asyncio.run(worker.execute(UUID(run_id))) is RefreshClaimOutcome.CLAIMED
            failed = client.get(f"{url}/{run_id}")
            assert failed.json()["state"] == "failed"
            assert failed.json()["error_code"] == "snapshot_writer_unavailable"
            assert asyncio.run(worker.execute(UUID(run_id))) is RefreshClaimOutcome.TERMINAL

            dispatcher.fail = True
            unavailable = client.post(
                url,
                headers={**_csrf(client), "If-Match": etag, "Idempotency-Key": "third"},
            )
            assert unavailable.status_code == 503
            retry_location = unavailable.headers["location"]
            dispatcher.fail = False
            retried = client.post(
                url,
                headers={**_csrf(client), "If-Match": etag, "Idempotency-Key": "third"},
            )
            assert retried.status_code == 202
            assert retried.headers["location"] == retry_location
            assert retried.json()["dispatch_attempts"] == 2

            changed = definition.copy()
            changed["fields"] = [dict(definition["fields"][0])]
            changed["fields"][0]["display"] = {
                **definition["fields"][0]["display"],
                "label": "Changed customer ID",
            }
            replaced = client.put(
                f"/api/v1/datasets/{dataset_id}/draft",
                json={"definition": changed},
                headers={**_csrf(client), "If-Match": etag},
            )
            assert replaced.status_code == 200
            stale_run_id = UUID(retried.json()["id"])
            stale_outcome = asyncio.run(worker.execute(stale_run_id))
            assert stale_outcome is RefreshClaimOutcome.TERMINAL
            stale = client.get(f"{url}/{stale_run_id}").json()
            assert stale["state"] == "failed"
            assert stale["error_code"] == "draft_changed"
    finally:
        asyncio.run(engine.dispose())

    async def inspect() -> tuple[int, int, list[AuditEventModel]]:
        audit_engine = create_database_engine(TEST_SETTINGS)
        try:
            async with create_session_factory(audit_engine)() as session:
                runs = len(list((await session.scalars(select(DatasetRefreshRunModel))).all()))
                requests = len(
                    list((await session.scalars(select(DatasetRefreshRequestModel))).all())
                )
                events = list((await session.scalars(select(AuditEventModel))).all())
                return runs, requests, events
        finally:
            await audit_engine.dispose()

    runs, requests, events = asyncio.run(inspect())
    assert runs == 2
    assert requests == 3
    assert "first" not in repr(events)
    assert "second" not in repr(events)
    assert "third" not in repr(events)


def test_worker_defers_successor_and_can_complete_with_injected_writer() -> None:
    asyncio.run(_bootstrap())
    engine = create_database_engine(TEST_SETTINGS)
    session_factory = create_session_factory(engine)
    store = SqlAlchemyRefreshRunStore(session_factory)
    dispatcher = RecordingDispatcher()
    service = RefreshRunService(
        store,
        dispatcher,
        SqlAlchemyDatasetStore(session_factory),
        ConnectionAccessAuthorizer(SqlAlchemyConnectionAccessStore(session_factory)),
    )
    try:
        with TestClient(create_app(settings=TEST_SETTINGS, refresh_run_service=service)) as client:
            _login(client)
            dataset_id, etag, definition = _create_dataset(client)
            asyncio.run(_mark_valid(dataset_id, definition))
            url = f"/api/v1/datasets/{dataset_id}/refresh-runs"
            first = client.post(
                url,
                headers={**_csrf(client), "If-Match": etag, "Idempotency-Key": "one"},
            ).json()
            claim, _ = asyncio.run(store.claim(UUID(first["id"]), datetime.now(UTC)))
            assert claim is RefreshClaimOutcome.CLAIMED
            second = client.post(
                url,
                headers={**_csrf(client), "If-Match": etag, "Idempotency-Key": "two"},
            ).json()
            assert first["id"] != second["id"]
            worker = RefreshWorkerService(store, SuccessfulExecutor())
            assert asyncio.run(worker.execute(UUID(second["id"]))) is RefreshClaimOutcome.DEFERRED
            asyncio.run(
                store.complete(
                    UUID(first["id"]),
                    RefreshExecutionResult(succeeded=False, error_code="test_completed"),
                    datetime.now(UTC),
                )
            )
            assert asyncio.run(worker.execute(UUID(second["id"]))) is RefreshClaimOutcome.CLAIMED
            completed = client.get(f"{url}/{second['id']}").json()
            assert completed["state"] == RefreshRunState.SUCCEEDED.value

            third = client.post(
                url,
                headers={**_csrf(client), "If-Match": etag, "Idempotency-Key": "three"},
            ).json()
            failing_worker = RefreshWorkerService(store, FailingExecutor())
            assert (
                asyncio.run(failing_worker.execute(UUID(third["id"])))
                is RefreshClaimOutcome.CLAIMED
            )
            failed = client.get(f"{url}/{third['id']}").json()
            assert failed["error_code"] == "refresh_execution_failed"
    finally:
        asyncio.run(engine.dispose())


def test_celery_contract_uses_json_late_ack_and_deterministic_task_id() -> None:
    app = create_celery_app(TEST_SETTINGS)
    assert app.conf.accept_content == ["json"]
    assert app.conf.task_acks_late is True
    assert app.conf.task_reject_on_worker_lost is True
    assert app.conf.worker_prefetch_multiplier == 1

    class FakeCelery:
        def __init__(self) -> None:
            self.call: dict[str, Any] = {}

        def send_task(self, name: str, **kwargs: Any) -> None:
            self.call = {"name": name, **kwargs}

    fake = FakeCelery()
    run_id = uuid4()
    CeleryRefreshDispatcher(cast(Any, fake)).dispatch(run_id)
    assert fake.call["args"] == [str(run_id)]
    assert fake.call["task_id"] == str(run_id)
    assert fake.call["queue"] == "refresh"
