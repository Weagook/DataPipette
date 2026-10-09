"""Idempotent refresh-run worker state machine."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from datepipette.domain.refresh import RefreshClaimOutcome, RefreshRun


@dataclass(frozen=True, slots=True)
class RefreshExecutionResult:
    succeeded: bool
    error_code: str | None = None


class RefreshWorkerStore(Protocol):
    async def claim(
        self,
        run_id: UUID,
        occurred_at: datetime,
    ) -> tuple[RefreshClaimOutcome, RefreshRun | None]: ...

    async def complete(
        self,
        run_id: UUID,
        result: RefreshExecutionResult,
        occurred_at: datetime,
    ) -> None: ...


class RefreshExecutor(Protocol):
    async def execute(self, run: RefreshRun) -> RefreshExecutionResult: ...


class UnavailableSnapshotWriter:
    async def execute(self, _run: RefreshRun) -> RefreshExecutionResult:
        return RefreshExecutionResult(
            succeeded=False,
            error_code="snapshot_writer_unavailable",
        )


class RefreshWorkerService:
    def __init__(self, store: RefreshWorkerStore, executor: RefreshExecutor) -> None:
        self._store = store
        self._executor = executor

    async def execute(self, run_id: UUID) -> RefreshClaimOutcome:
        outcome, run = await self._store.claim(run_id, datetime.now(UTC))
        if outcome is not RefreshClaimOutcome.CLAIMED or run is None:
            return outcome
        try:
            result = await self._executor.execute(run)
        except Exception:
            result = RefreshExecutionResult(
                succeeded=False,
                error_code="refresh_execution_failed",
            )
        await self._store.complete(run_id, result, datetime.now(UTC))
        return outcome
