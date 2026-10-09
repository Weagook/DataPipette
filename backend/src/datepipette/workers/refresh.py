"""Celery entry points for dataset refresh runs."""

import asyncio
from uuid import UUID

from celery import Task  # type: ignore[import-untyped]

from datepipette.application.refresh_worker import RefreshWorkerService
from datepipette.config import get_settings
from datepipette.domain.refresh import RefreshClaimOutcome
from datepipette.infrastructure.celery_app import celery_app
from datepipette.infrastructure.crypto import AesGcmSecretCipher
from datepipette.infrastructure.database import create_database_engine, create_session_factory
from datepipette.infrastructure.parquet_snapshots import LocalParquetSnapshotExecutor
from datepipette.infrastructure.repositories.refresh_runs import SqlAlchemyRefreshRunStore


async def _execute(run_id: UUID) -> RefreshClaimOutcome:
    settings = get_settings()
    engine = create_database_engine(settings)
    try:
        session_factory = create_session_factory(engine)
        service = RefreshWorkerService(
            SqlAlchemyRefreshRunStore(session_factory),
            LocalParquetSnapshotExecutor(
                session_factory,
                AesGcmSecretCipher(settings.connection_encryption_key.get_secret_value()),
                settings.snapshot_storage_path,
                settings.source_connect_timeout_seconds,
                settings.refresh_statement_timeout_seconds,
                settings.refresh_batch_rows,
            ),
        )
        return await service.execute(run_id)
    finally:
        await engine.dispose()


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    name="datepipette.refresh.execute",
    acks_late=True,
    ignore_result=True,
    max_retries=None,
)
def execute_refresh(task: Task, run_id: str) -> None:
    outcome = asyncio.run(_execute(UUID(run_id)))
    if outcome is RefreshClaimOutcome.DEFERRED:
        raise task.retry(countdown=5)


@celery_app.task(  # type: ignore[untyped-decorator]
    name="datepipette.worker.health",
    ignore_result=True,
)
def worker_health() -> str:
    return "ok"
