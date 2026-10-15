"""Durable refresh request and worker-state integration tests."""

import asyncio
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

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
from datepipette.application.schedules import RefreshSchedulerService
from datepipette.config import Settings
from datepipette.domain.refresh import RefreshClaimOutcome, RefreshRun, RefreshRunState
from datepipette.domain.snapshots import SnapshotArtifact
from datepipette.infrastructure.celery_app import create_celery_app
from datepipette.infrastructure.database import create_database_engine, create_session_factory
from datepipette.infrastructure.models import (
    AuditEventModel,
    DatasetRefreshRequestModel,
    DatasetRefreshRunModel,
    DatasetRefreshScheduleModel,
    DatasetVersionModel,
)
from datepipette.infrastructure.refresh_dispatch import CeleryRefreshDispatcher
from datepipette.infrastructure.repositories.authentication import SqlAlchemyAuthenticationStore
from datepipette.infrastructure.repositories.connection_access import (
    SqlAlchemyConnectionAccessStore,
)
from datepipette.infrastructure.repositories.datasets import SqlAlchemyDatasetStore
from datepipette.infrastructure.repositories.refresh_runs import SqlAlchemyRefreshRunStore
from datepipette.infrastructure.repositories.schedules import SqlAlchemyRefreshScheduleStore
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
    async def execute(
        self,
        run: RefreshRun,
        _cancellation: asyncio.Event,
    ) -> RefreshExecutionResult:
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
    async def execute(
        self,
        _run: RefreshRun,
        _cancellation: asyncio.Event,
    ) -> RefreshExecutionResult:
        raise RuntimeError("sensitive infrastructure detail")


class RetryableExecutor:
    async def execute(
        self,
        _run: RefreshRun,
        _cancellation: asyncio.Event,
    ) -> RefreshExecutionResult:
        return RefreshExecutionResult(
            False,
            "source_unavailable",
            retryable=True,
        )


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
                "plan": {
                    "node_type": "Seq Scan",
                    "estimated_rows": 2,
                    "startup_cost": 0.0,
                    "total_cost": 1.0,
                },
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
            worker = RefreshWorkerService(store, SuccessfulExecutor(), policy_revision=4)
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
            assert completed["policy_revision"] == 4
            assert completed["planner_estimated_rows"] == 2
            assert completed["actual_rows"] == 1
            assert completed["execution_duration_ms"] >= 0
            assert completed["row_estimate_ratio"] == 0.5

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


def test_refresh_cancellation_is_idempotent_and_wins_before_publication() -> None:
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

            queued = client.post(
                url,
                headers={**_csrf(client), "If-Match": etag, "Idempotency-Key": "cancel-queued"},
            ).json()
            missing_csrf = client.post(f"{url}/{queued['id']}/cancel")
            assert missing_csrf.status_code == 403
            cancelled = client.post(
                f"{url}/{queued['id']}/cancel",
                headers=_csrf(client),
            )
            assert cancelled.status_code == 200
            assert cancelled.json()["state"] == "cancelled"
            assert cancelled.json()["cancel_requested_at"] is not None
            assert cancelled.json()["cancelled_at"] is not None
            repeated = client.post(
                f"{url}/{queued['id']}/cancel",
                headers=_csrf(client),
            )
            assert repeated.status_code == 200
            assert repeated.json()["cancelled_at"] == cancelled.json()["cancelled_at"]
            assert (
                asyncio.run(
                    RefreshWorkerService(store, SuccessfulExecutor()).execute(UUID(queued["id"]))
                )
                is RefreshClaimOutcome.TERMINAL
            )

            running = client.post(
                url,
                headers={**_csrf(client), "If-Match": etag, "Idempotency-Key": "cancel-running"},
            ).json()
            claim, claimed = asyncio.run(store.claim(UUID(running["id"]), datetime.now(UTC)))
            assert claim is RefreshClaimOutcome.CLAIMED
            assert claimed is not None
            requested = client.post(
                f"{url}/{running['id']}/cancel",
                headers=_csrf(client),
            )
            assert requested.status_code == 200
            assert requested.json()["state"] == "running"
            assert requested.json()["cancel_requested_at"] is not None
            assert requested.json()["cancelled_at"] is None
            repeated_running = client.post(
                f"{url}/{running['id']}/cancel",
                headers=_csrf(client),
            )
            assert repeated_running.status_code == 200
            assert (
                repeated_running.json()["cancel_requested_at"]
                == requested.json()["cancel_requested_at"]
            )
            artifact = asyncio.run(SuccessfulExecutor().execute(claimed, asyncio.Event()))
            asyncio.run(store.complete(UUID(running["id"]), artifact, datetime.now(UTC)))
            terminal = client.get(f"{url}/{running['id']}").json()
            assert terminal["state"] == "cancelled"
            assert terminal["snapshot_id"] is None
            assert (
                client.post(
                    f"{url}/{running['id']}/cancel",
                    headers=_csrf(client),
                ).status_code
                == 200
            )

            succeeded = client.post(
                url,
                headers={**_csrf(client), "If-Match": etag, "Idempotency-Key": "complete"},
            ).json()
            asyncio.run(
                RefreshWorkerService(store, SuccessfulExecutor()).execute(UUID(succeeded["id"]))
            )
            conflict = client.post(
                f"{url}/{succeeded['id']}/cancel",
                headers=_csrf(client),
            )
            assert conflict.status_code == 409
            assert conflict.json()["code"] == "refresh_run_not_cancellable"

            async def cancellation_actions() -> list[str]:
                async with session_factory() as session:
                    return list(
                        await session.scalars(
                            select(AuditEventModel.action).where(
                                AuditEventModel.action.in_(
                                    [
                                        "dataset.refresh_cancellation_requested",
                                        "dataset.refresh_cancelled",
                                    ]
                                )
                            )
                        )
                    )

            actions = asyncio.run(cancellation_actions())
            assert actions.count("dataset.refresh_cancellation_requested") == 1
            assert actions.count("dataset.refresh_cancelled") == 2
    finally:
        asyncio.run(engine.dispose())


def test_retry_policy_is_durable_bounded_and_yields_to_a_successor() -> None:
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
    schedule_store = SqlAlchemyRefreshScheduleStore(session_factory, "UTC")
    scheduler = RefreshSchedulerService(schedule_store, dispatcher, ZoneInfo("UTC"), 100)
    started = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)

    def clock(*values: datetime) -> Any:
        moments = iter(values)
        return lambda: next(moments)

    try:
        with TestClient(create_app(settings=TEST_SETTINGS, refresh_run_service=service)) as client:
            _login(client)
            dataset_id, etag, definition = _create_dataset(client)
            asyncio.run(_mark_valid(dataset_id, definition))
            url = f"/api/v1/datasets/{dataset_id}/refresh-runs"
            first = client.post(
                url,
                headers={**_csrf(client), "If-Match": etag, "Idempotency-Key": "retry-success"},
            ).json()
            dispatcher.run_ids.clear()
            first_worker = RefreshWorkerService(
                store,
                RetryableExecutor(),
                max_attempts=3,
                retry_base_seconds=5,
                retry_max_seconds=30,
                clock=clock(started, started),
            )
            assert (
                asyncio.run(first_worker.execute(UUID(first["id"]))) is RefreshClaimOutcome.CLAIMED
            )
            queued = client.get(f"{url}/{first['id']}").json()
            assert queued["state"] == "queued"
            assert queued["attempt_count"] == 1
            assert queued["error_code"] == "source_unavailable"
            assert datetime.fromisoformat(queued["next_attempt_at"]) == started + timedelta(
                seconds=5
            )
            freshness = client.get(f"/api/v1/datasets/{dataset_id}/freshness").json()
            assert freshness["data_state"] == "refreshing"
            assert freshness["refresh_state"] == "queued"
            assert freshness["last_refresh_error"] == "source_unavailable"
            assert (
                datetime.fromisoformat(freshness["last_refresh_error_at"].replace("Z", "+00:00"))
                == started
            )

            early = asyncio.run(scheduler.tick(started + timedelta(seconds=4)))
            assert early.dispatch_attempts == 0
            due = asyncio.run(scheduler.tick(started + timedelta(seconds=5)))
            assert due.dispatch_attempts == 1
            assert dispatcher.run_ids == [UUID(first["id"])]
            second_worker = RefreshWorkerService(
                store,
                SuccessfulExecutor(),
                clock=clock(started + timedelta(seconds=5), started + timedelta(seconds=6)),
            )
            assert (
                asyncio.run(second_worker.execute(UUID(first["id"]))) is RefreshClaimOutcome.CLAIMED
            )
            completed = client.get(f"{url}/{first['id']}").json()
            assert completed["state"] == "succeeded"
            assert completed["attempt_count"] == 2
            assert completed["next_attempt_at"] is None

            exhausted = client.post(
                url,
                headers={**_csrf(client), "If-Match": etag, "Idempotency-Key": "retry-exhausted"},
            ).json()
            for attempt in range(2):
                attempt_at = started + timedelta(minutes=attempt + 1)
                worker = RefreshWorkerService(
                    store,
                    RetryableExecutor(),
                    max_attempts=2,
                    retry_base_seconds=1,
                    retry_max_seconds=1,
                    clock=clock(attempt_at, attempt_at),
                )
                asyncio.run(worker.execute(UUID(exhausted["id"])))
            failed = client.get(f"{url}/{exhausted['id']}").json()
            assert failed["state"] == "failed"
            assert failed["attempt_count"] == 2
            assert failed["next_attempt_at"] is None

            older = client.post(
                url,
                headers={**_csrf(client), "If-Match": etag, "Idempotency-Key": "older"},
            ).json()
            claim, _ = asyncio.run(store.claim(UUID(older["id"]), started + timedelta(hours=1)))
            assert claim is RefreshClaimOutcome.CLAIMED
            successor = client.post(
                url,
                headers={**_csrf(client), "If-Match": etag, "Idempotency-Key": "successor"},
            ).json()
            assert successor["id"] != older["id"]
            asyncio.run(
                store.complete(
                    UUID(older["id"]),
                    RefreshExecutionResult(False, "source_unavailable", retryable=True),
                    started + timedelta(hours=1, seconds=1),
                    started + timedelta(hours=1, seconds=6),
                )
            )
            older_terminal = client.get(f"{url}/{older['id']}").json()
            assert older_terminal["state"] == "failed"
            assert client.get(f"{url}/{successor['id']}").json()["state"] == "queued"

            async def retry_actions() -> list[str]:
                async with session_factory() as session:
                    return list(
                        await session.scalars(
                            select(AuditEventModel.action).where(
                                AuditEventModel.action == "dataset.refresh_retry_scheduled"
                            )
                        )
                    )

            assert len(asyncio.run(retry_actions())) == 2
    finally:
        asyncio.run(engine.dispose())


def test_stale_running_lease_is_recovered_and_attempts_remain_bounded() -> None:
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
    schedule_store = SqlAlchemyRefreshScheduleStore(session_factory, "UTC")
    scheduler = RefreshSchedulerService(schedule_store, dispatcher, ZoneInfo("UTC"), 100)
    started = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
    try:
        with TestClient(create_app(settings=TEST_SETTINGS, refresh_run_service=service)) as client:
            _login(client)
            dataset_id, etag, definition = _create_dataset(client)
            asyncio.run(_mark_valid(dataset_id, definition))
            url = f"/api/v1/datasets/{dataset_id}/refresh-runs"
            run = client.post(
                url,
                headers={**_csrf(client), "If-Match": etag, "Idempotency-Key": "lease"},
            ).json()
            run_id = UUID(run["id"])
            first, claimed = asyncio.run(
                store.claim(
                    run_id,
                    started,
                    started + timedelta(seconds=10),
                    3,
                )
            )
            assert first is RefreshClaimOutcome.CLAIMED
            assert claimed is not None and claimed.attempt_count == 1
            dispatcher.run_ids.clear()

            assert (
                asyncio.run(scheduler.tick(started + timedelta(seconds=9))).dispatch_attempts == 0
            )
            stale = asyncio.run(scheduler.tick(started + timedelta(seconds=10)))
            assert stale.dispatch_attempts == 1
            assert dispatcher.run_ids == [run_id]
            reclaimed, second = asyncio.run(
                store.claim(
                    run_id,
                    started + timedelta(seconds=10),
                    started + timedelta(seconds=20),
                    3,
                )
            )
            assert reclaimed is RefreshClaimOutcome.CLAIMED
            assert second is not None and second.attempt_count == 2
            assert (
                asyncio.run(
                    store.heartbeat(
                        run_id,
                        started + timedelta(seconds=15),
                        started + timedelta(seconds=30),
                    )
                )
                is False
            )
            assert (
                asyncio.run(scheduler.tick(started + timedelta(seconds=29))).dispatch_attempts == 0
            )

            third_claim, third = asyncio.run(
                store.claim(
                    run_id,
                    started + timedelta(seconds=30),
                    started + timedelta(seconds=40),
                    3,
                )
            )
            assert third_claim is RefreshClaimOutcome.CLAIMED
            assert third is not None and third.attempt_count == 3
            exhausted, terminal = asyncio.run(
                store.claim(
                    run_id,
                    started + timedelta(seconds=40),
                    started + timedelta(seconds=50),
                    3,
                )
            )
            assert exhausted is RefreshClaimOutcome.TERMINAL
            assert terminal is not None
            assert terminal.state is RefreshRunState.FAILED
            assert terminal.error_code == "worker_lost"

            async def lease_actions() -> list[str]:
                async with session_factory() as session:
                    return list(
                        await session.scalars(
                            select(AuditEventModel.action).where(
                                AuditEventModel.resource_id == run_id
                            )
                        )
                    )

            actions = asyncio.run(lease_actions())
            assert actions.count("dataset.refresh_reclaimed") == 2
            assert actions.count("dataset.refresh_failed") == 1
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


def test_schedule_api_and_scheduler_create_coalesce_and_recover_runs() -> None:
    asyncio.run(_bootstrap())
    engine = create_database_engine(TEST_SETTINGS)
    session_factory = create_session_factory(engine)
    run_store = SqlAlchemyRefreshRunStore(session_factory)
    dispatcher = RecordingDispatcher()
    refresh_service = RefreshRunService(
        run_store,
        dispatcher,
        SqlAlchemyDatasetStore(session_factory),
        ConnectionAccessAuthorizer(SqlAlchemyConnectionAccessStore(session_factory)),
    )
    schedule_store = SqlAlchemyRefreshScheduleStore(session_factory, "UTC")
    scheduler = RefreshSchedulerService(schedule_store, dispatcher, ZoneInfo("UTC"), 100)

    async def force_due(dataset_id: UUID, when: datetime) -> None:
        async with session_factory() as session, session.begin():
            schedule = await session.scalar(
                select(DatasetRefreshScheduleModel).where(
                    DatasetRefreshScheduleModel.dataset_id == dataset_id
                )
            )
            assert schedule is not None
            schedule.next_run_at = when

    try:
        with TestClient(
            create_app(settings=TEST_SETTINGS, refresh_run_service=refresh_service)
        ) as client:
            _login(client)
            dataset_id_text, draft_etag, definition = _create_dataset(client)
            dataset_id = UUID(dataset_id_text)
            schedule_url = f"/api/v1/datasets/{dataset_id}/refresh-schedule"
            assert client.get(schedule_url).status_code == 404

            disabled = client.put(
                schedule_url,
                json={
                    "kind": "interval",
                    "interval_minutes": 15,
                    "daily_time": None,
                    "enabled": False,
                },
                headers=_csrf(client),
            )
            assert disabled.status_code == 200, disabled.text
            assert disabled.headers["etag"] == '"schedule-1"'
            assert disabled.json()["timezone"] == "UTC"
            assert disabled.json()["next_run_at"] is None

            unpublished = client.put(
                schedule_url,
                json={
                    "kind": "interval",
                    "interval_minutes": 15,
                    "daily_time": None,
                    "enabled": True,
                },
                headers={**_csrf(client), "If-Match": disabled.headers["etag"]},
            )
            assert unpublished.status_code == 409
            assert unpublished.json()["code"] == "dataset_not_published"

            asyncio.run(_mark_valid(dataset_id_text, definition))
            manual = client.post(
                f"/api/v1/datasets/{dataset_id}/refresh-runs",
                headers={
                    **_csrf(client),
                    "If-Match": draft_etag,
                    "Idempotency-Key": "publish-before-scheduling",
                },
            ).json()
            assert (
                asyncio.run(
                    RefreshWorkerService(run_store, SuccessfulExecutor()).execute(
                        UUID(manual["id"])
                    )
                )
                is RefreshClaimOutcome.CLAIMED
            )
            active_version_id = client.get(f"/api/v1/datasets/{dataset_id}").json()[
                "active_version_id"
            ]

            missing_precondition = client.put(
                schedule_url,
                json={
                    "kind": "daily",
                    "interval_minutes": None,
                    "daily_time": "09:30:00",
                    "enabled": True,
                },
                headers=_csrf(client),
            )
            assert missing_precondition.status_code == 428
            enabled = client.put(
                schedule_url,
                json={
                    "kind": "daily",
                    "interval_minutes": None,
                    "daily_time": "09:30:00",
                    "enabled": True,
                },
                headers={**_csrf(client), "If-Match": disabled.headers["etag"]},
            )
            assert enabled.status_code == 200, enabled.text
            assert enabled.headers["etag"] == '"schedule-2"'
            assert enabled.json()["next_run_at"] is not None
            stale = client.put(
                schedule_url,
                json={
                    "kind": "daily",
                    "interval_minutes": None,
                    "daily_time": "10:00:00",
                    "enabled": True,
                },
                headers={**_csrf(client), "If-Match": disabled.headers["etag"]},
            )
            assert stale.status_code == 409

            dispatcher.run_ids.clear()
            due_at = datetime(2026, 10, 11, 8, 0, tzinfo=UTC)
            asyncio.run(force_due(dataset_id, due_at))
            tick = asyncio.run(scheduler.tick(due_at))
            assert tick.due_schedules == 1
            assert tick.dispatch_attempts == 1
            scheduled_run_id = dispatcher.run_ids[-1]
            scheduled_run = client.get(
                f"/api/v1/datasets/{dataset_id}/refresh-runs/{scheduled_run_id}"
            ).json()
            assert scheduled_run["trigger_type"] == "scheduled"
            assert scheduled_run["schedule_id"] == enabled.json()["id"]
            assert scheduled_run["dataset_version_id"] == active_version_id

            assert (
                asyncio.run(
                    RefreshWorkerService(run_store, SuccessfulExecutor()).execute(scheduled_run_id)
                )
                is RefreshClaimOutcome.CLAIMED
            )
            dispatcher.fail = True
            second_due = datetime(2026, 10, 12, 8, 0, tzinfo=UTC)
            asyncio.run(force_due(dataset_id, second_due))
            failed_dispatch = asyncio.run(scheduler.tick(second_due))
            assert failed_dispatch.dispatch_failures == 1
            pending = asyncio.run(schedule_store.recoverable_run_ids(now=second_due, limit=10))
            assert len(pending) == 1
            undispatched_id = pending[0]
            dispatcher.fail = False
            recovered = asyncio.run(scheduler.tick(second_due))
            assert recovered.due_schedules == 0
            assert recovered.dispatch_attempts == 1
            assert dispatcher.run_ids[-1] == undispatched_id

            claim, _ = asyncio.run(run_store.claim(undispatched_id, second_due))
            assert claim is RefreshClaimOutcome.CLAIMED
            third_due = datetime(2026, 10, 13, 8, 0, tzinfo=UTC)
            asyncio.run(force_due(dataset_id, third_due))
            successor = asyncio.run(scheduler.tick(third_due))
            assert successor.due_schedules == 1
            assert successor.dispatch_attempts == 2
            dispatched = dispatcher.run_ids[-2:]
            assert undispatched_id in dispatched
            successor_id = next(run_id for run_id in dispatched if run_id != undispatched_id)
            assert successor_id != undispatched_id
            stale_claim, stale_run = asyncio.run(run_store.claim(undispatched_id, third_due))
            assert stale_claim is RefreshClaimOutcome.TERMINAL
            assert stale_run is not None
            assert stale_run.error_code == "worker_lost"

            fourth_due = datetime(2026, 10, 14, 8, 0, tzinfo=UTC)
            asyncio.run(force_due(dataset_id, fourth_due))
            coalesced = asyncio.run(scheduler.tick(fourth_due))
            assert coalesced.due_schedules == 1
            assert coalesced.dispatch_attempts == 0
    finally:
        asyncio.run(engine.dispose())
