"""Celery entry point for dataset exports."""

import asyncio
from uuid import UUID

from datepipette.application.exports import ExportWorkerService
from datepipette.config import get_settings
from datepipette.infrastructure.celery_app import celery_app
from datepipette.infrastructure.database import create_database_engine, create_session_factory
from datepipette.infrastructure.local_exports import LocalDatasetExportExecutor
from datepipette.infrastructure.repositories.exports import SqlAlchemyExportStore


async def _execute(export_id: UUID) -> None:
    settings = get_settings()
    engine = create_database_engine(settings)
    try:
        store = SqlAlchemyExportStore(create_session_factory(engine))
        service = ExportWorkerService(
            store,
            LocalDatasetExportExecutor(
                settings.snapshot_storage_path,
                memory_limit_mb=settings.export_memory_limit_mb,
                threads=settings.export_threads,
                temporary_directory_limit_mb=settings.export_temp_limit_mb,
                maximum_rows=settings.export_max_rows,
                fetch_rows=settings.export_fetch_rows,
            ),
        )
        await service.execute(export_id)
    finally:
        await engine.dispose()


@celery_app.task(  # type: ignore[untyped-decorator]
    name="datepipette.export.execute",
    acks_late=True,
    ignore_result=True,
)
def execute_export(export_id: str) -> None:
    asyncio.run(_execute(UUID(export_id)))
