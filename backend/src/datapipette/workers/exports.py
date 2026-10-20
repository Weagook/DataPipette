"""Celery entry point for dataset exports."""

import asyncio
from uuid import UUID

from celery import Task  # type: ignore[import-untyped]

from datapipette.application.exports import ExportWorkerService
from datapipette.config import get_settings
from datapipette.domain.exports import ExportClaimOutcome
from datapipette.infrastructure.background_admission import PostgresBackgroundJobAdmission
from datapipette.infrastructure.celery_app import celery_app
from datapipette.infrastructure.database import create_database_engine, create_session_factory
from datapipette.infrastructure.local_exports import DatasetExportExecutor
from datapipette.infrastructure.object_storage import create_object_storage_registry
from datapipette.infrastructure.repositories.exports import SqlAlchemyExportStore
from datapipette.infrastructure.repositories.query_safety import SqlAlchemyQuerySafetyPolicyStore


async def _execute(export_id: UUID) -> ExportClaimOutcome:
    settings = get_settings()
    engine = create_database_engine(settings)
    try:
        session_factory = create_session_factory(engine)
        store = SqlAlchemyExportStore(session_factory)
        policy = await SqlAlchemyQuerySafetyPolicyStore(
            session_factory,
            settings.default_query_safety_policy(),
        ).get()
        object_storages = create_object_storage_registry(settings)
        async with PostgresBackgroundJobAdmission(engine).slot(
            policy.policy.background_job_concurrency
        ) as admitted:
            if not admitted:
                return ExportClaimOutcome.DEFERRED
            service = ExportWorkerService(
                store,
                DatasetExportExecutor(
                    object_storages,
                    settings.snapshot_storage_path,
                    memory_limit_mb=policy.policy.export_memory_limit_mb,
                    threads=policy.policy.export_threads,
                    temporary_directory_limit_mb=policy.policy.export_temp_limit_mb,
                    maximum_rows=policy.policy.export_max_rows,
                    maximum_bytes=policy.policy.export_max_bytes,
                    timeout_seconds=policy.policy.export_timeout_seconds,
                    fetch_rows=settings.export_fetch_rows,
                ),
                policy_revision=policy.revision,
            )
            return await service.execute(export_id)
    finally:
        await engine.dispose()


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    name="datapipette.export.execute",
    acks_late=True,
    ignore_result=True,
)
def execute_export(task: Task, export_id: str) -> None:
    outcome = asyncio.run(_execute(UUID(export_id)))
    if outcome is ExportClaimOutcome.DEFERRED:
        raise task.retry(countdown=5)
