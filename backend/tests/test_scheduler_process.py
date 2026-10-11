"""Independent scheduler process wiring."""

import asyncio
from typing import Any

import pytest

from datepipette import scheduler
from datepipette.application.schedules import SchedulerTickResult
from datepipette.application.storage_cleanup import CleanupTickResult
from datepipette.config import Settings


class StopScheduler(Exception):
    pass


class FakeEngine:
    def __init__(self) -> None:
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True


class FakeRefreshScheduler:
    async def tick(self) -> SchedulerTickResult:
        return SchedulerTickResult(1, 1, 0)


class FakeCleanupScheduler:
    async def tick(self) -> CleanupTickResult:
        return CleanupTickResult(1, 1, 0, 2, 1)


def test_scheduler_runs_refresh_and_cleanup_ticks_then_disposes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        environment="test",
        scheduler_poll_seconds=1,
        cleanup_interval_seconds=60,
    )
    engine = FakeEngine()
    refresh = FakeRefreshScheduler()
    cleanup = FakeCleanupScheduler()
    clock = iter((0.0, 0.0, 0.0))

    monkeypatch.setattr(scheduler, "get_settings", lambda: settings)
    monkeypatch.setattr(scheduler, "configure_logging", lambda _level: None)
    monkeypatch.setattr(scheduler, "create_database_engine", lambda _settings: engine)
    monkeypatch.setattr(scheduler, "create_session_factory", lambda _engine: object())
    monkeypatch.setattr(scheduler, "SqlAlchemyRefreshScheduleStore", lambda *_args: object())
    monkeypatch.setattr(scheduler, "SqlAlchemyStorageCleanupStore", lambda *_args: object())
    monkeypatch.setattr(scheduler, "CeleryRefreshDispatcher", lambda *_args: object())
    monkeypatch.setattr(scheduler, "RefreshSchedulerService", lambda *_args: refresh)
    monkeypatch.setattr(
        scheduler,
        "StorageCleanupService",
        lambda *_args, **_kwargs: cleanup,
    )
    monkeypatch.setattr(scheduler, "create_object_storage_registry", lambda _settings: object())
    monkeypatch.setattr(scheduler, "monotonic", lambda: next(clock))

    async def stop_after_tick(_seconds: float) -> Any:
        raise StopScheduler

    monkeypatch.setattr(asyncio, "sleep", stop_after_tick)

    with pytest.raises(StopScheduler):
        asyncio.run(scheduler.serve())

    assert engine.disposed is True
