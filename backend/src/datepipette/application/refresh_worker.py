"""Idempotent refresh-run worker state machine."""

import asyncio
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from datepipette.domain.refresh import RefreshClaimOutcome, RefreshRun
from datepipette.domain.snapshots import SnapshotArtifact


@dataclass(frozen=True, slots=True)
class RefreshExecutionResult:
    succeeded: bool
    error_code: str | None = None
    snapshot: SnapshotArtifact | None = None
    retryable: bool = False
    cancelled: bool = False


class RefreshWorkerStore(Protocol):
    async def claim(
        self,
        run_id: UUID,
        occurred_at: datetime,
        lease_expires_at: datetime,
        max_attempts: int,
    ) -> tuple[RefreshClaimOutcome, RefreshRun | None]: ...

    async def complete(
        self,
        run_id: UUID,
        result: RefreshExecutionResult,
        occurred_at: datetime,
        retry_at: datetime | None,
    ) -> None: ...

    async def is_cancel_requested(self, run_id: UUID) -> bool: ...

    async def heartbeat(
        self,
        run_id: UUID,
        occurred_at: datetime,
        lease_expires_at: datetime,
    ) -> bool: ...


class RefreshExecutor(Protocol):
    async def execute(
        self,
        run: RefreshRun,
        cancellation: asyncio.Event,
    ) -> RefreshExecutionResult: ...


class UnavailableSnapshotWriter:
    async def execute(
        self,
        _run: RefreshRun,
        _cancellation: asyncio.Event,
    ) -> RefreshExecutionResult:
        return RefreshExecutionResult(
            succeeded=False,
            error_code="snapshot_writer_unavailable",
        )


class RefreshWorkerService:
    def __init__(
        self,
        store: RefreshWorkerStore,
        executor: RefreshExecutor,
        *,
        max_attempts: int = 3,
        retry_base_seconds: int = 5,
        retry_max_seconds: int = 300,
        cancellation_poll_seconds: float = 1.0,
        run_lease_seconds: int = 30,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._executor = executor
        self._max_attempts = max_attempts
        self._retry_base_seconds = retry_base_seconds
        self._retry_max_seconds = retry_max_seconds
        self._cancellation_poll_seconds = cancellation_poll_seconds
        self._run_lease_seconds = run_lease_seconds
        self._clock = clock or (lambda: datetime.now(UTC))

    async def execute(self, run_id: UUID) -> RefreshClaimOutcome:
        claimed_at = self._clock()
        outcome, run = await self._store.claim(
            run_id,
            claimed_at,
            claimed_at + timedelta(seconds=self._run_lease_seconds),
            self._max_attempts,
        )
        if outcome is not RefreshClaimOutcome.CLAIMED or run is None:
            return outcome
        cancellation = asyncio.Event()
        if await self._cancellation_requested(run_id):
            cancellation.set()
        monitor = asyncio.create_task(self._monitor_cancellation(run_id, cancellation))
        try:
            result = await self._executor.execute(run, cancellation)
        except Exception:
            result = RefreshExecutionResult(
                succeeded=False,
                error_code="refresh_execution_failed",
            )
        finally:
            monitor.cancel()
            with suppress(asyncio.CancelledError):
                await monitor
        completed_at = self._clock()
        retry_at = None
        if result.retryable and not result.cancelled and run.attempt_count < self._max_attempts:
            delay = min(
                self._retry_max_seconds,
                self._retry_base_seconds * (2 ** (run.attempt_count - 1)),
            )
            retry_at = completed_at + timedelta(seconds=delay)
        await self._store.complete(run_id, result, completed_at, retry_at)
        return outcome

    async def _monitor_cancellation(
        self,
        run_id: UUID,
        cancellation: asyncio.Event,
    ) -> None:
        while not cancellation.is_set():
            await asyncio.sleep(self._cancellation_poll_seconds)
            observed_at = self._clock()
            if await self._heartbeat(
                run_id,
                observed_at,
                observed_at + timedelta(seconds=self._run_lease_seconds),
            ):
                cancellation.set()

    async def _cancellation_requested(self, run_id: UUID) -> bool:
        try:
            return await self._store.is_cancel_requested(run_id)
        except Exception:
            return False

    async def _heartbeat(
        self,
        run_id: UUID,
        occurred_at: datetime,
        lease_expires_at: datetime,
    ) -> bool:
        try:
            return await self._store.heartbeat(run_id, occurred_at, lease_expires_at)
        except Exception:
            return False
