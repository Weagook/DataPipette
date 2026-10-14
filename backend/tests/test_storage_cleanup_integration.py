"""Metadata and local-object lifecycle cleanup integration."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select, text

from datepipette.application.storage_cleanup import StorageCleanupService
from datepipette.config import Settings
from datepipette.infrastructure.database import create_database_engine, create_session_factory
from datepipette.infrastructure.models import (
    AuditEventModel,
    DatasetExportModel,
    DatasetModel,
    DatasetRefreshRunModel,
    DatasetSnapshotModel,
    DatasetVersionModel,
    PostgresConnectionModel,
    UserModel,
)
from datepipette.infrastructure.object_storage import (
    LocalObjectStorage,
    ObjectStorageRegistry,
)
from datepipette.infrastructure.repositories.storage_cleanup import (
    SqlAlchemyStorageCleanupStore,
)

SETTINGS = Settings(environment="test")


async def _reset() -> None:
    engine = create_database_engine(SETTINGS)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("TRUNCATE object_cleanup_cursors, users RESTART IDENTITY CASCADE")
            )
    finally:
        await engine.dispose()


async def _seed(now: datetime) -> dict[str, Any]:
    engine = create_database_engine(SETTINGS)
    factory = create_session_factory(engine)
    user_id = uuid4()
    connection_id = uuid4()
    dataset_id = uuid4()
    version_id = uuid4()
    active_run_id = uuid4()
    superseded_run_id = uuid4()
    active_snapshot_id = uuid4()
    superseded_snapshot_id = uuid4()
    expired_export_id = uuid4()
    future_export_id = uuid4()
    try:
        async with factory() as session, session.begin():
            session.add(
                UserModel(
                    id=user_id,
                    email="cleanup@example.com",
                    display_name="Cleanup",
                    password_hash="unused",
                    role="admin",
                    status="active",
                    last_login_at=None,
                    created_at=now,
                    updated_at=now,
                    account_type="human",
                )
            )
            await session.flush()
            session.add(
                PostgresConnectionModel(
                    id=connection_id,
                    name="Cleanup source",
                    host="source.invalid",
                    port=5432,
                    database_name="analytics",
                    username="readonly",
                    encrypted_password="unused",
                    encrypted_ca_certificate=None,
                    ssl_mode="disable",
                    created_by=user_id,
                    last_tested_at=None,
                    last_test_succeeded=None,
                    last_test_error_code=None,
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.flush()
            session.add(
                DatasetModel(
                    id=dataset_id,
                    name="Cleanup dataset",
                    owner_id=user_id,
                    connection_id=connection_id,
                    active_version_id=None,
                    active_snapshot_id=None,
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.flush()
            session.add(
                DatasetVersionModel(
                    id=version_id,
                    dataset_id=dataset_id,
                    state="published",
                    revision=1,
                    definition={"schema_version": 1},
                    validation=None,
                    created_by=user_id,
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.flush()
            for run_id in (active_run_id, superseded_run_id):
                session.add(
                    DatasetRefreshRunModel(
                        id=run_id,
                        dataset_id=dataset_id,
                        dataset_version_id=version_id,
                        requested_revision=1,
                        state="succeeded",
                        requested_by=user_id,
                        trigger_type="manual",
                        schedule_id=None,
                        dispatch_attempts=1,
                        dispatched_at=now,
                        attempt_count=1,
                        error_code=None,
                        requested_at=now,
                        started_at=now,
                        finished_at=now,
                        updated_at=now,
                    )
                )
            await session.flush()
            active_key = f"datasets/{dataset_id}/snapshots/{active_snapshot_id}.parquet"
            superseded_key = f"datasets/{dataset_id}/snapshots/{superseded_snapshot_id}.parquet"
            for snapshot_id, run_id, state, key, superseded_at in (
                (active_snapshot_id, active_run_id, "active", active_key, None),
                (
                    superseded_snapshot_id,
                    superseded_run_id,
                    "superseded",
                    superseded_key,
                    now - timedelta(hours=2),
                ),
            ):
                session.add(
                    DatasetSnapshotModel(
                        id=snapshot_id,
                        dataset_id=dataset_id,
                        dataset_version_id=version_id,
                        refresh_run_id=run_id,
                        state=state,
                        storage_backend="local",
                        object_key=key,
                        row_count=1,
                        byte_size=4,
                        checksum_sha256="0" * 64,
                        schema_document={"schema_version": 1},
                        created_at=now,
                        activated_at=now,
                        superseded_at=superseded_at,
                        purged_at=None,
                        cleanup_claimed_at=None,
                    )
                )
            await session.flush()
            dataset = await session.get(DatasetModel, dataset_id)
            assert dataset is not None
            dataset.active_version_id = version_id
            dataset.active_snapshot_id = active_snapshot_id
            for export_id, expiry, key in (
                (
                    expired_export_id,
                    now - timedelta(hours=1),
                    f"exports/{dataset_id}/{expired_export_id}.csv",
                ),
                (
                    future_export_id,
                    now + timedelta(hours=1),
                    f"exports/{dataset_id}/{future_export_id}.csv",
                ),
            ):
                session.add(
                    DatasetExportModel(
                        id=export_id,
                        dataset_id=dataset_id,
                        snapshot_id=superseded_snapshot_id,
                        requested_by=user_id,
                        format="csv",
                        state="succeeded",
                        query_document={},
                        dispatch_attempts=1,
                        dispatched_at=now,
                        attempt_count=1,
                        object_key=key,
                        storage_backend="local",
                        row_count=1,
                        byte_size=4,
                        checksum_sha256="0" * 64,
                        content_type="text/csv",
                        error_code=None,
                        request_id="cleanup-test",
                        requested_at=now,
                        started_at=now,
                        finished_at=now,
                        expires_at=expiry,
                        updated_at=now,
                        purged_at=None,
                        cleanup_claimed_at=None,
                    )
                )
        return {
            "active_snapshot_id": active_snapshot_id,
            "active_key": active_key,
            "superseded_snapshot_id": superseded_snapshot_id,
            "superseded_key": superseded_key,
            "expired_export_id": expired_export_id,
            "expired_key": f"exports/{dataset_id}/{expired_export_id}.csv",
            "future_export_id": future_export_id,
            "future_key": f"exports/{dataset_id}/{future_export_id}.csv",
        }
    finally:
        await engine.dispose()


def test_cleanup_preserves_active_and_referenced_snapshots_then_purges(tmp_path: Path) -> None:
    asyncio.run(_reset())
    now = datetime(2026, 10, 11, 12, tzinfo=UTC)
    ids = asyncio.run(_seed(now))
    orphan_key = "datasets/orphan/snapshots/unreferenced.parquet"
    for key in (
        str(ids["active_key"]),
        str(ids["superseded_key"]),
        str(ids["expired_key"]),
        str(ids["future_key"]),
        orphan_key,
    ):
        path = tmp_path / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"data")
        old = (now - timedelta(days=2)).timestamp()
        os.utime(path, (old, old))

    engine = create_database_engine(SETTINGS)
    factory = create_session_factory(engine)
    service = StorageCleanupService(
        SqlAlchemyStorageCleanupStore(factory),
        ObjectStorageRegistry("local", {"local": LocalObjectStorage(tmp_path)}),
        batch_size=100,
        snapshot_grace_seconds=3600,
        export_grace_seconds=300,
        orphan_grace_seconds=86_400,
    )
    try:
        first = asyncio.run(service.tick(now))
        assert first.purged_objects == 1
        assert first.purged_orphans == 1
        assert (tmp_path / str(ids["active_key"])).exists()
        assert (tmp_path / str(ids["superseded_key"])).exists()
        assert not (tmp_path / str(ids["expired_key"])).exists()
        assert (tmp_path / str(ids["future_key"])).exists()
        assert not (tmp_path / orphan_key).exists()

        async def expire_and_inspect() -> tuple[Any, Any, tuple[Any, list[AuditEventModel]]]:
            async with factory() as session, session.begin():
                future = await session.get(
                    DatasetExportModel,
                    ids["future_export_id"],
                    with_for_update=True,
                )
                assert future is not None
                future.expires_at = now - timedelta(hours=1)
            second = await service.tick(now)
            async with factory() as session:
                snapshot = await session.get(DatasetSnapshotModel, ids["superseded_snapshot_id"])
                future = await session.get(DatasetExportModel, ids["future_export_id"])
                events = list(
                    (
                        await session.scalars(
                            select(AuditEventModel).where(AuditEventModel.action.like("storage.%"))
                        )
                    ).all()
                )
                return second, snapshot, (future, events)

        second, snapshot, future_and_events = asyncio.run(expire_and_inspect())
        future, events = future_and_events
        assert second.purged_objects == 2
        assert snapshot is not None and snapshot.purged_at == now
        assert future is not None and future.purged_at == now
        assert not (tmp_path / str(ids["superseded_key"])).exists()
        assert not (tmp_path / str(ids["future_key"])).exists()
        assert (tmp_path / str(ids["active_key"])).exists()
        assert {event.action for event in events} >= {
            "storage.export_purged",
            "storage.snapshot_purged",
            "storage.orphans_purged",
        }
    finally:
        asyncio.run(engine.dispose())
        asyncio.run(_reset())
