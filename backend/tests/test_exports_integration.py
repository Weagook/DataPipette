"""Public API and metadata lifecycle for snapshot-bound exports."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from datepipette.api.dependencies.authentication import CSRF_COOKIE_NAME
from datepipette.application.access_control import DatasetAccessAuthorizer
from datepipette.application.auth_service import bootstrap_first_admin
from datepipette.application.exports import (
    DatasetExportService,
    ExportDownloadService,
    ExportWorkerService,
)
from datepipette.config import Settings
from datepipette.domain.exports import ExportClaimOutcome
from datepipette.infrastructure.database import create_database_engine, create_session_factory
from datepipette.infrastructure.export_tokens import EncryptedExportDownloadTokenCodec
from datepipette.infrastructure.local_exports import LocalDatasetExportExecutor
from datepipette.infrastructure.models import (
    AuditEventModel,
    DatasetModel,
    DatasetRefreshRunModel,
    DatasetSnapshotModel,
    DatasetVersionModel,
    PostgresConnectionModel,
    UserModel,
)
from datepipette.infrastructure.object_storage import (
    LocalObjectStorage,
    ObjectExportDownloadResolver,
    ObjectStorageRegistry,
)
from datepipette.infrastructure.repositories.access_control import SqlAlchemyAccessControlStore
from datepipette.infrastructure.repositories.authentication import SqlAlchemyAuthenticationStore
from datepipette.infrastructure.repositories.exports import SqlAlchemyExportStore
from datepipette.main import create_app

ADMIN_EMAIL = "export-admin@example.com"
ADMIN_PASSWORD = "correct horse battery staple"


class RecordingDispatcher:
    def __init__(self) -> None:
        self.ids: list[object] = []

    def dispatch(self, export_id: object) -> None:
        self.ids.append(export_id)


async def _reset(settings: Settings) -> None:
    engine = create_database_engine(settings)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("TRUNCATE users RESTART IDENTITY CASCADE"))
    finally:
        await engine.dispose()


def _seed(settings: Settings, root: Path) -> tuple[str, str, str, str]:
    field_id = uuid4()
    dataset_id = uuid4()
    snapshot_id = uuid4()
    version_id = uuid4()
    run_id = uuid4()
    connection_id = uuid4()
    now = datetime.now(UTC)
    pq.write_table(
        pa.table({f"field_{field_id.hex}": pa.array([1, 2], type=pa.int64())}),
        root / "snapshot.parquet",
    )

    async def seed() -> str:
        engine = create_database_engine(settings)
        factory = create_session_factory(engine)
        try:
            await bootstrap_first_admin(
                SqlAlchemyAuthenticationStore(factory),
                ADMIN_EMAIL,
                "Export Admin",
                ADMIN_PASSWORD,
            )
            async with factory() as session, session.begin():
                user_id = await session.scalar(
                    select(UserModel.id).where(UserModel.email == ADMIN_EMAIL)
                )
                assert user_id is not None
                session.add(
                    PostgresConnectionModel(
                        id=connection_id,
                        name="Export source",
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
                        name="Exportable",
                        owner_id=user_id,
                        connection_id=connection_id,
                        active_version_id=None,
                        active_snapshot_id=None,
                        created_at=now,
                        updated_at=now,
                    )
                )
                await session.flush()
                relation_id = uuid4()
                session.add(
                    DatasetVersionModel(
                        id=version_id,
                        dataset_id=dataset_id,
                        state="published",
                        revision=1,
                        definition={
                            "schema_version": 1,
                            "source": {
                                "relations": [
                                    {
                                        "id": str(relation_id),
                                        "schema": "public",
                                        "name": "records",
                                        "alias": "records",
                                    }
                                ]
                            },
                            "fields": [
                                {
                                    "id": str(field_id),
                                    "source": {"relation_id": str(relation_id), "column": "id"},
                                    "name": "record_id",
                                    "display": {
                                        "label": "Record ID",
                                        "format": None,
                                        "visible": True,
                                    },
                                }
                            ],
                            "joins": [],
                        },
                        validation={
                            "state": "valid",
                            "validated_revision": 1,
                            "validated_at": now.isoformat(),
                            "compiled_sql": "SELECT 1",
                            "result_schema": [
                                {
                                    "field_id": str(field_id),
                                    "name": "record_id",
                                    "data_type": "bigint",
                                    "nullable": False,
                                }
                            ],
                            "plan": None,
                            "findings": [],
                        },
                        created_by=user_id,
                        created_at=now,
                        updated_at=now,
                    )
                )
                await session.flush()
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
                session.add(
                    DatasetSnapshotModel(
                        id=snapshot_id,
                        dataset_id=dataset_id,
                        dataset_version_id=version_id,
                        refresh_run_id=run_id,
                        state="active",
                        storage_backend="local",
                        object_key="snapshot.parquet",
                        row_count=2,
                        byte_size=(root / "snapshot.parquet").stat().st_size,
                        checksum_sha256="0" * 64,
                        schema_document={
                            "schema_version": 2,
                            "fields": [
                                {"name": "record_id", "storage_name": f"field_{field_id.hex}"}
                            ],
                        },
                        created_at=now,
                        activated_at=now,
                        superseded_at=None,
                    )
                )
                await session.flush()
                dataset = await session.get(DatasetModel, dataset_id)
                assert dataset is not None
                dataset.active_version_id = version_id
                dataset.active_snapshot_id = snapshot_id
                return str(user_id)
        finally:
            await engine.dispose()

    user_id = asyncio.run(seed())
    return str(dataset_id), str(snapshot_id), str(field_id), user_id


def test_export_api_worker_history_and_reissued_download(tmp_path: Path) -> None:
    settings = Settings(environment="test", docs_enabled=False, snapshot_storage_path=tmp_path)
    asyncio.run(_reset(settings))
    dataset_id, snapshot_id, field_id, _ = _seed(settings, tmp_path)
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    store = SqlAlchemyExportStore(session_factory)
    authorizer = DatasetAccessAuthorizer(SqlAlchemyAccessControlStore(session_factory))
    dispatcher = RecordingDispatcher()
    export_service = DatasetExportService(store, dispatcher, authorizer)
    local_storage = LocalObjectStorage(tmp_path)
    download_service = ExportDownloadService(
        store,
        EncryptedExportDownloadTokenCodec(settings.connection_encryption_key.get_secret_value()),
        ObjectExportDownloadResolver(
            ObjectStorageRegistry(
                local_storage.backend,
                {local_storage.backend: local_storage},
            )
        ),
        authorizer,
    )
    app = create_app(
        settings=settings,
        export_service=export_service,
        export_download_service=download_service,
    )
    try:
        with TestClient(app) as client:
            login = client.post(
                "/api/v1/auth/sessions", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
            )
            assert login.status_code == 201
            csrf = {"X-CSRF-Token": client.cookies[CSRF_COOKIE_NAME]}
            url = f"/api/v1/datasets/{dataset_id}/exports"
            missing_key = client.post(
                url, headers=csrf, json={"format": "csv", "fields": [field_id]}
            )
            assert missing_key.status_code == 428
            created = client.post(
                url,
                headers={**csrf, "Idempotency-Key": "export-once"},
                json={
                    "format": "csv",
                    "fields": [field_id],
                    "sorts": [{"field_id": field_id, "direction": "desc"}],
                },
            )
            assert created.status_code == 202, created.text
            body = created.json()
            assert body["snapshot_id"] == snapshot_id
            assert dispatcher.ids
            repeated = client.post(
                url,
                headers={**csrf, "Idempotency-Key": "export-once"},
                json={"format": "csv", "fields": [field_id]},
            )
            assert repeated.json()["id"] == body["id"]
            assert repeated.headers["x-datepipette-idempotent-replay"] == "true"
            not_ready = client.post(f"{url}/{body['id']}/download-link", headers=csrf)
            assert not_ready.status_code == 409

            worker = ExportWorkerService(
                store,
                LocalDatasetExportExecutor(
                    tmp_path,
                    memory_limit_mb=64,
                    threads=1,
                    temporary_directory_limit_mb=64,
                    maximum_rows=100,
                    fetch_rows=10,
                ),
                policy_revision=3,
            )
            assert asyncio.run(worker.execute(uuid4())) is ExportClaimOutcome.NOT_FOUND
            assert asyncio.run(worker.execute(UUID(body["id"]))) is ExportClaimOutcome.CLAIMED
            completed = client.get(f"{url}/{body['id']}")
            assert completed.status_code == 200
            assert completed.json()["state"] == "succeeded"
            assert completed.json()["row_count"] == 2
            assert completed.json()["policy_revision"] == 3
            history = client.get(url)
            assert history.status_code == 200
            assert history.json()["data"][0]["id"] == body["id"]
            first_link = client.post(f"{url}/{body['id']}/download-link", headers=csrf).json()
            second_link = client.post(f"{url}/{body['id']}/download-link", headers=csrf).json()
            assert first_link["download_url"] != second_link["download_url"]
            downloaded = client.get(first_link["download_url"])
            assert downloaded.status_code == 200
            assert downloaded.content.startswith(b"\xef\xbb\xbfrecord_id")
            assert (
                client.get("/api/v1/export-downloads?token=invalid-token-value-000").status_code
                == 404
            )
            assert client.get(f"{url}/{uuid4()}").status_code == 404

        async def inspect() -> list[AuditEventModel]:
            async with create_session_factory(engine)() as session:
                return list(
                    (
                        await session.scalars(
                            select(AuditEventModel).where(
                                AuditEventModel.action.like("dataset.export_%")
                            )
                        )
                    ).all()
                )

        events = asyncio.run(inspect())
        assert {event.action for event in events} >= {
            "dataset.export_requested",
            "dataset.export_started",
            "dataset.export_succeeded",
        }
        governed = [
            event
            for event in events
            if event.action in {"dataset.export_started", "dataset.export_succeeded"}
        ]
        assert governed
        assert all(event.details["policy_revision"] == 3 for event in governed)
        assert "export-once" not in repr(events)
    finally:
        asyncio.run(engine.dispose())
        asyncio.run(_reset(settings))
