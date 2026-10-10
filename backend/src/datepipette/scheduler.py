"""Independent polling process for durable dataset refresh schedules."""

import asyncio
import logging
from zoneinfo import ZoneInfo

from datepipette.application.schedules import RefreshSchedulerService
from datepipette.config import get_settings
from datepipette.infrastructure.celery_app import celery_app
from datepipette.infrastructure.database import create_database_engine, create_session_factory
from datepipette.infrastructure.refresh_dispatch import CeleryRefreshDispatcher
from datepipette.infrastructure.repositories.schedules import SqlAlchemyRefreshScheduleStore
from datepipette.logging import configure_logging

logger = logging.getLogger(__name__)


async def serve() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    engine = create_database_engine(settings)
    try:
        store = SqlAlchemyRefreshScheduleStore(
            create_session_factory(engine),
            settings.installation_timezone,
        )
        service = RefreshSchedulerService(
            store,
            CeleryRefreshDispatcher(celery_app),
            ZoneInfo(settings.installation_timezone),
            settings.scheduler_batch_size,
        )
        while True:
            try:
                result = await service.tick()
                if result.dispatch_attempts or result.due_schedules:
                    logger.info(
                        "scheduler_tick_completed",
                        extra={
                            "due_schedules": result.due_schedules,
                            "dispatch_attempts": result.dispatch_attempts,
                            "dispatch_failures": result.dispatch_failures,
                        },
                    )
            except Exception:
                logger.error("scheduler_tick_failed")
            await asyncio.sleep(settings.scheduler_poll_seconds)
    finally:
        await engine.dispose()


def run() -> None:
    asyncio.run(serve())
