"""Focused cooperative refresh worker behavior."""

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

from datepipette.application.refresh_worker import (
    RefreshExecutionResult,
    RefreshWorkerService,
)
from datepipette.domain.refresh import (
    RefreshClaimOutcome,
    RefreshRun,
    RefreshRunState,
    RefreshTriggerType,
)


def _run(now: datetime) -> RefreshRun:
    return RefreshRun(
        id=uuid4(),
        dataset_id=uuid4(),
        dataset_version_id=uuid4(),
        requested_revision=1,
        state=RefreshRunState.RUNNING,
        requested_by=uuid4(),
        trigger_type=RefreshTriggerType.MANUAL,
        schedule_id=None,
        dispatch_attempts=1,
        dispatched_at=now,
        attempt_count=1,
        error_code=None,
        next_attempt_at=None,
        cancel_requested_at=None,
        cancelled_at=None,
        lease_expires_at=None,
        snapshot_id=None,
        requested_at=now,
        started_at=now,
        finished_at=None,
        updated_at=now,
    )


class CancellingStore:
    def __init__(self, run: RefreshRun) -> None:
        self.run = run
        self.probes = 0
        self.completed: tuple[RefreshExecutionResult, datetime | None] | None = None

    async def claim(
        self,
        run_id: UUID,
        occurred_at: datetime,
        lease_expires_at: datetime,
        max_attempts: int,
        policy_revision: int,
    ) -> tuple[RefreshClaimOutcome, RefreshRun | None]:
        del run_id, occurred_at, lease_expires_at, max_attempts, policy_revision
        return RefreshClaimOutcome.CLAIMED, self.run

    async def is_cancel_requested(self, run_id: UUID) -> bool:
        del run_id
        self.probes += 1
        return self.probes >= 2

    async def heartbeat(
        self,
        run_id: UUID,
        occurred_at: datetime,
        lease_expires_at: datetime,
    ) -> bool:
        del occurred_at, lease_expires_at
        return await self.is_cancel_requested(run_id)

    async def complete(
        self,
        run_id: UUID,
        result: RefreshExecutionResult,
        occurred_at: datetime,
        retry_at: datetime | None,
    ) -> None:
        del run_id, occurred_at
        self.completed = result, retry_at


class CooperativeExecutor:
    async def execute(
        self,
        run: RefreshRun,
        cancellation: asyncio.Event,
    ) -> RefreshExecutionResult:
        del run
        await asyncio.wait_for(cancellation.wait(), timeout=1)
        return RefreshExecutionResult(False, cancelled=True)


def test_worker_propagates_durable_cancellation_to_executor() -> None:
    now = datetime(2026, 10, 11, tzinfo=UTC)
    store = CancellingStore(_run(now))
    monotonic_values = iter((10.0, 10.125))
    worker = RefreshWorkerService(
        store,
        CooperativeExecutor(),
        cancellation_poll_seconds=0.001,
        clock=lambda: now,
        monotonic_clock=lambda: next(monotonic_values),
    )

    outcome = asyncio.run(worker.execute(store.run.id))

    assert outcome is RefreshClaimOutcome.CLAIMED
    assert store.probes >= 2
    assert store.completed is not None
    result, retry_at = store.completed
    assert result.cancelled is True
    assert result.execution_duration_ms == 125
    assert retry_at is None
