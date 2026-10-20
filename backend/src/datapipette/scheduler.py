"""Independent polling process for durable dataset refresh schedules."""

import asyncio
import logging
from time import monotonic
from zoneinfo import ZoneInfo

from datapipette.application.schedules import RefreshSchedulerService
from datapipette.application.storage_cleanup import StorageCleanupService
from datapipette.config import get_settings
from datapipette.infrastructure.celery_app import celery_app
from datapipette.infrastructure.database import create_database_engine, create_session_factory
from datapipette.infrastructure.object_storage import create_object_storage_registry
from datapipette.infrastructure.refresh_dispatch import CeleryRefreshDispatcher
from datapipette.infrastructure.repositories.schedules import SqlAlchemyRefreshScheduleStore
from datapipette.infrastructure.repositories.storage_cleanup import SqlAlchemyStorageCleanupStore
from datapipette.logging import configure_logging

logger = logging.getLogger(__name__)


async def serve() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    engine = create_database_engine(settings)
    try:
        session_factory = create_session_factory(engine)
        store = SqlAlchemyRefreshScheduleStore(
            session_factory,
            settings.installation_timezone,
        )
        service = RefreshSchedulerService(
            store,
            CeleryRefreshDispatcher(celery_app),
            ZoneInfo(settings.installation_timezone),
            settings.scheduler_batch_size,
        )
        cleanup_service = StorageCleanupService(
            SqlAlchemyStorageCleanupStore(session_factory),
            create_object_storage_registry(settings),
            batch_size=settings.cleanup_batch_size,
            snapshot_grace_seconds=settings.cleanup_snapshot_grace_seconds,
            export_grace_seconds=settings.cleanup_export_grace_seconds,
            orphan_grace_seconds=settings.cleanup_orphan_grace_seconds,
        )
        next_cleanup_at = monotonic()
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
            if settings.cleanup_enabled and monotonic() >= next_cleanup_at:
                try:
                    cleanup = await cleanup_service.tick()
                    if cleanup.purged_objects or cleanup.purged_orphans or cleanup.failed_objects:
                        logger.info(
                            "storage_cleanup_tick_completed",
                            extra={
                                "claimed_objects": cleanup.claimed_objects,
                                "purged_objects": cleanup.purged_objects,
                                "failed_objects": cleanup.failed_objects,
                                "scanned_objects": cleanup.scanned_objects,
                                "purged_orphans": cleanup.purged_orphans,
                            },
                        )
                except Exception:
                    logger.error("storage_cleanup_tick_failed")
                finally:
                    next_cleanup_at = monotonic() + settings.cleanup_interval_seconds
            await asyncio.sleep(settings.scheduler_poll_seconds)
    finally:
        await engine.dispose()


def run() -> None:
    asyncio.run(serve())
