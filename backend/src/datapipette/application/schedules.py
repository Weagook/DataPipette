"""Owner-managed refresh schedules and durable scheduler polling."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from datapipette.application.datasets import (
    DatasetNotFoundError,
    DatasetStore,
    require_dataset_owner,
)
from datapipette.application.refresh_runs import RefreshDispatcher
from datapipette.domain.identity import User
from datapipette.domain.schedules import (
    DatasetRefreshSchedule,
    RefreshScheduleSpec,
    next_occurrence,
)


class RefreshScheduleNotFoundError(Exception):
    pass


class RefreshScheduleRevisionRequiredError(Exception):
    pass


class RefreshScheduleRevisionConflictError(Exception):
    pass


class RefreshScheduleRequiresPublishedDatasetError(Exception):
    pass


class RefreshScheduleStore(Protocol):
    async def get(self, dataset_id: UUID) -> DatasetRefreshSchedule | None: ...

    async def replace(
        self,
        *,
        schedule_id: UUID,
        dataset_id: UUID,
        actor_id: UUID,
        spec: RefreshScheduleSpec,
        enabled: bool,
        timezone: str,
        next_run_at: datetime | None,
        expected_revision: int | None,
        occurred_at: datetime,
        request_id: str,
    ) -> DatasetRefreshSchedule: ...


class SchedulerStore(Protocol):
    async def recoverable_run_ids(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> tuple[UUID, ...]: ...

    async def claim_due(
        self,
        *,
        now: datetime,
        timezone: ZoneInfo,
        limit: int,
    ) -> "DueScheduleClaims": ...

    async def record_dispatch_attempt(self, run_id: UUID, occurred_at: datetime) -> None: ...

    async def mark_dispatched(self, run_id: UUID, occurred_at: datetime) -> None: ...


@dataclass(frozen=True, slots=True)
class SchedulerTickResult:
    due_schedules: int
    dispatch_attempts: int
    dispatch_failures: int


@dataclass(frozen=True, slots=True)
class DueScheduleClaims:
    due_schedules: int
    run_ids: tuple[UUID, ...]


class RefreshScheduleService:
    def __init__(
        self,
        store: RefreshScheduleStore,
        dataset_store: DatasetStore,
        timezone: ZoneInfo,
    ) -> None:
        self._store = store
        self._dataset_store = dataset_store
        self._timezone = timezone

    async def get(self, *, actor: User, dataset_id: UUID) -> DatasetRefreshSchedule:
        dataset = await self._dataset_store.get(dataset_id)
        if dataset is None:
            raise RefreshScheduleNotFoundError
        try:
            require_dataset_owner(actor, dataset)
        except DatasetNotFoundError as exc:
            raise RefreshScheduleNotFoundError from exc
        schedule = await self._store.get(dataset_id)
        if schedule is None:
            raise RefreshScheduleNotFoundError
        return schedule

    async def replace(
        self,
        *,
        actor: User,
        dataset_id: UUID,
        spec: RefreshScheduleSpec,
        enabled: bool,
        expected_revision: int | None,
        request_id: str,
    ) -> DatasetRefreshSchedule:
        dataset = await self._dataset_store.get(dataset_id)
        if dataset is None:
            raise RefreshScheduleNotFoundError
        try:
            require_dataset_owner(actor, dataset)
        except DatasetNotFoundError as exc:
            raise RefreshScheduleNotFoundError from exc
        if enabled and dataset.active_version_id is None:
            raise RefreshScheduleRequiresPublishedDatasetError
        now = datetime.now(UTC)
        return await self._store.replace(
            schedule_id=uuid4(),
            dataset_id=dataset_id,
            actor_id=actor.id,
            spec=spec,
            enabled=enabled,
            timezone=self._timezone.key,
            next_run_at=(
                next_occurrence(spec, after=now, timezone=self._timezone) if enabled else None
            ),
            expected_revision=expected_revision,
            occurred_at=now,
            request_id=request_id,
        )


class RefreshSchedulerService:
    def __init__(
        self,
        store: SchedulerStore,
        dispatcher: RefreshDispatcher,
        timezone: ZoneInfo,
        batch_size: int,
    ) -> None:
        self._store = store
        self._dispatcher = dispatcher
        self._timezone = timezone
        self._batch_size = batch_size

    async def tick(self, now: datetime | None = None) -> SchedulerTickResult:
        occurred_at = (now or datetime.now(UTC)).astimezone(UTC)
        pending = await self._store.recoverable_run_ids(
            now=occurred_at,
            limit=self._batch_size,
        )
        due = await self._store.claim_due(
            now=occurred_at,
            timezone=self._timezone,
            limit=self._batch_size,
        )
        run_ids = tuple(dict.fromkeys((*pending, *due.run_ids)))[: self._batch_size]
        failures = 0
        for run_id in run_ids:
            await self._store.record_dispatch_attempt(run_id, occurred_at)
            try:
                self._dispatcher.dispatch(run_id)
            except Exception:
                failures += 1
                continue
            await self._store.mark_dispatched(run_id, occurred_at)
        return SchedulerTickResult(
            due_schedules=due.due_schedules,
            dispatch_attempts=len(run_ids),
            dispatch_failures=failures,
        )
