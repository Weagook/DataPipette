"""Celery entry points for dataset refresh runs."""

import asyncio
from uuid import UUID

from celery import Task  # type: ignore[import-untyped]

from datapipette.application.connection_access import ConnectionAccessAuthorizer
from datapipette.application.refresh_worker import RefreshWorkerService
from datapipette.config import get_settings
from datapipette.domain.refresh import RefreshClaimOutcome
from datapipette.infrastructure.background_admission import PostgresBackgroundJobAdmission
from datapipette.infrastructure.celery_app import celery_app
from datapipette.infrastructure.crypto import AesGcmSecretCipher
from datapipette.infrastructure.database import create_database_engine, create_session_factory
from datapipette.infrastructure.object_storage import create_object_storage_registry
from datapipette.infrastructure.parquet_snapshots import ParquetSnapshotExecutor
from datapipette.infrastructure.repositories.connection_access import (
    SqlAlchemyConnectionAccessStore,
)
from datapipette.infrastructure.repositories.query_safety import SqlAlchemyQuerySafetyPolicyStore
from datapipette.infrastructure.repositories.refresh_runs import SqlAlchemyRefreshRunStore


async def _execute(run_id: UUID) -> RefreshClaimOutcome:
    settings = get_settings()
    engine = create_database_engine(settings)
    try:
        session_factory = create_session_factory(engine)
        connection_authorizer = ConnectionAccessAuthorizer(
            SqlAlchemyConnectionAccessStore(session_factory)
        )
        object_storages = create_object_storage_registry(settings)
        policy = await SqlAlchemyQuerySafetyPolicyStore(
            session_factory,
            settings.default_query_safety_policy(),
        ).get()
        async with PostgresBackgroundJobAdmission(engine).slot(
            policy.policy.background_job_concurrency
        ) as admitted:
            if not admitted:
                return RefreshClaimOutcome.DEFERRED
            service = RefreshWorkerService(
                SqlAlchemyRefreshRunStore(session_factory),
                ParquetSnapshotExecutor(
                    session_factory,
                    AesGcmSecretCipher(settings.connection_encryption_key.get_secret_value()),
                    object_storages.default,
                    settings.snapshot_storage_path,
                    settings.source_connect_timeout_seconds,
                    policy.policy.refresh_statement_timeout_seconds,
                    settings.refresh_batch_rows,
                    connection_authorizer,
                    maximum_rows=policy.policy.refresh_max_rows,
                ),
                max_attempts=settings.refresh_max_attempts,
                retry_base_seconds=settings.refresh_retry_base_seconds,
                retry_max_seconds=settings.refresh_retry_max_seconds,
                cancellation_poll_seconds=settings.refresh_cancel_poll_seconds,
                run_lease_seconds=settings.refresh_run_lease_seconds,
                policy_revision=policy.revision,
            )
            return await service.execute(run_id)
    finally:
        await engine.dispose()


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    name="datapipette.refresh.execute",
    acks_late=True,
    ignore_result=True,
    max_retries=None,
)
def execute_refresh(task: Task, run_id: str) -> None:
    outcome = asyncio.run(_execute(UUID(run_id)))
    if outcome is RefreshClaimOutcome.DEFERRED:
        raise task.retry(countdown=5)


@celery_app.task(  # type: ignore[untyped-decorator]
    name="datapipette.worker.health",
    ignore_result=True,
)
def worker_health() -> str:
    return "ok"
