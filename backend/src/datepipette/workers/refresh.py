"""Celery entry points for dataset refresh runs."""

import asyncio
from uuid import UUID

from celery import Task  # type: ignore[import-untyped]

from datepipette.application.refresh_worker import RefreshWorkerService, UnavailableSnapshotWriter
from datepipette.config import get_settings
from datepipette.domain.refresh import RefreshClaimOutcome
from datepipette.infrastructure.celery_app import celery_app
from datepipette.infrastructure.database import create_database_engine, create_session_factory
from datepipette.infrastructure.repositories.refresh_runs import SqlAlchemyRefreshRunStore


async def _execute(run_id: UUID) -> RefreshClaimOutcome:
    engine = create_database_engine(get_settings())
    try:
        service = RefreshWorkerService(
            SqlAlchemyRefreshRunStore(create_session_factory(engine)),
            UnavailableSnapshotWriter(),
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
