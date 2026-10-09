"""Transactional persistence for durable dataset refresh runs."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from datepipette.application.datasets import DatasetRevisionConflictError
from datepipette.application.refresh_runs import RefreshDraftNotValidatedError
from datepipette.application.refresh_worker import RefreshExecutionResult
from datepipette.domain.audit import AuditActorType, AuditOutcome
from datepipette.domain.datasets import (
    DatasetValidation,
    DatasetValidationState,
    DatasetVersionState,
)
from datepipette.domain.refresh import (
    RefreshClaimOutcome,
    RefreshRequestOutcome,
    RefreshRun,
    RefreshRunState,
)
from datepipette.infrastructure.models import (
    AuditEventModel,
    DatasetModel,
    DatasetRefreshRequestModel,
    DatasetRefreshRunModel,
    DatasetVersionModel,
)


def _to_run(model: DatasetRefreshRunModel) -> RefreshRun:
    return RefreshRun(
        id=model.id,
        dataset_id=model.dataset_id,
        dataset_version_id=model.dataset_version_id,
        requested_revision=model.requested_revision,
        state=RefreshRunState(model.state),
        requested_by=model.requested_by,
        dispatch_attempts=model.dispatch_attempts,
        dispatched_at=model.dispatched_at,
        attempt_count=model.attempt_count,
        error_code=model.error_code,
        requested_at=model.requested_at,
        started_at=model.started_at,
        finished_at=model.finished_at,
        updated_at=model.updated_at,
    )


def _validation_is_current(model: DatasetVersionModel) -> bool:
    if model.validation is None:
        return False
    validation = DatasetValidation.from_document(model.validation)
    return validation.validated_revision == model.revision and validation.state in {
        DatasetValidationState.VALID,
        DatasetValidationState.VALID_WITH_WARNINGS,
    }


class SqlAlchemyRefreshRunStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def request(
        self,
        *,
        dataset_id: UUID,
        actor_id: UUID,
        expected_revision: int,
        idempotency_key_hash: str,
        refresh_run_id: UUID,
        occurred_at: datetime,
        request_id: str,
    ) -> RefreshRequestOutcome | None:
        async with self._session_factory() as session, session.begin():
            dataset = await session.scalar(
                select(DatasetModel).where(DatasetModel.id == dataset_id).with_for_update()
            )
            if dataset is None:
                return None
            existing_request = await session.scalar(
                select(DatasetRefreshRequestModel).where(
                    DatasetRefreshRequestModel.dataset_id == dataset_id,
                    DatasetRefreshRequestModel.actor_id == actor_id,
                    DatasetRefreshRequestModel.idempotency_key_hash == idempotency_key_hash,
                )
            )
            if existing_request is not None:
                existing_run = await session.get(
                    DatasetRefreshRunModel,
                    existing_request.refresh_run_id,
                )
                if existing_run is None:  # pragma: no cover - protected by foreign key
                    raise RuntimeError("Refresh request points to a missing run")
                return RefreshRequestOutcome(
                    run=_to_run(existing_run),
                    coalesced=True,
                    dispatch_required=(
                        existing_run.state == RefreshRunState.QUEUED.value
                        and existing_run.dispatched_at is None
                    ),
                )

            draft = await session.scalar(
                select(DatasetVersionModel)
                .where(
                    DatasetVersionModel.dataset_id == dataset_id,
                    DatasetVersionModel.state == DatasetVersionState.DRAFT.value,
                )
                .with_for_update()
            )
            if draft is None:
                return None
            if draft.revision != expected_revision:
                raise DatasetRevisionConflictError
            if not _validation_is_current(draft):
                raise RefreshDraftNotValidatedError

            queued = await session.scalar(
                select(DatasetRefreshRunModel)
                .where(
                    DatasetRefreshRunModel.dataset_id == dataset_id,
                    DatasetRefreshRunModel.state == RefreshRunState.QUEUED.value,
                )
                .with_for_update()
            )
            if queued is not None and queued.requested_revision != draft.revision:
                self._fail(queued, "draft_changed", occurred_at)
                session.add(
                    self._audit(
                        actor_id=None,
                        action="dataset.refresh_failed",
                        dataset_id=dataset_id,
                        run_id=queued.id,
                        occurred_at=occurred_at,
                        request_id=request_id,
                        details={"error_code": "draft_changed"},
                        outcome=AuditOutcome.FAILURE,
                    )
                )
                queued = None

            coalesced = queued is not None
            run = queued or DatasetRefreshRunModel(
                id=refresh_run_id,
                dataset_id=dataset_id,
                dataset_version_id=draft.id,
                requested_revision=draft.revision,
                state=RefreshRunState.QUEUED.value,
                requested_by=actor_id,
                dispatch_attempts=0,
                dispatched_at=None,
                attempt_count=0,
                error_code=None,
                requested_at=occurred_at,
                started_at=None,
                finished_at=None,
                updated_at=occurred_at,
            )
            if not coalesced:
                session.add(run)
                await session.flush()
            session.add(
                DatasetRefreshRequestModel(
                    id=uuid4(),
                    dataset_id=dataset_id,
                    actor_id=actor_id,
                    idempotency_key_hash=idempotency_key_hash,
                    refresh_run_id=run.id,
                    requested_at=occurred_at,
                )
            )
            session.add(
                self._audit(
                    actor_id=actor_id,
                    action=(
                        "dataset.refresh_coalesced" if coalesced else "dataset.refresh_requested"
                    ),
                    dataset_id=dataset_id,
                    run_id=run.id,
                    occurred_at=occurred_at,
                    request_id=request_id,
                    details={"revision": draft.revision},
                )
            )
            await session.flush()
            return RefreshRequestOutcome(
                run=_to_run(run),
                coalesced=coalesced,
                dispatch_required=run.dispatched_at is None,
            )

    async def get(self, *, dataset_id: UUID, run_id: UUID) -> RefreshRun | None:
        async with self._session_factory() as session:
            model = await session.scalar(
                select(DatasetRefreshRunModel).where(
                    DatasetRefreshRunModel.id == run_id,
                    DatasetRefreshRunModel.dataset_id == dataset_id,
                )
            )
        return _to_run(model) if model is not None else None

    async def record_dispatch_attempt(self, run_id: UUID, occurred_at: datetime) -> None:
        async with self._session_factory() as session, session.begin():
            model = await session.get(DatasetRefreshRunModel, run_id, with_for_update=True)
            if model is not None and model.state == RefreshRunState.QUEUED.value:
                model.dispatch_attempts += 1
                model.updated_at = occurred_at

    async def mark_dispatched(self, run_id: UUID, occurred_at: datetime) -> None:
        async with self._session_factory() as session, session.begin():
            model = await session.get(DatasetRefreshRunModel, run_id, with_for_update=True)
            if model is not None and model.state == RefreshRunState.QUEUED.value:
                model.dispatched_at = occurred_at
                model.updated_at = occurred_at

    async def claim(
        self,
        run_id: UUID,
        occurred_at: datetime,
    ) -> tuple[RefreshClaimOutcome, RefreshRun | None]:
        async with self._session_factory() as session, session.begin():
            model = await session.get(DatasetRefreshRunModel, run_id, with_for_update=True)
            if model is None:
                return RefreshClaimOutcome.NOT_FOUND, None
            if model.state != RefreshRunState.QUEUED.value:
                return RefreshClaimOutcome.TERMINAL, _to_run(model)
            draft = await session.get(DatasetVersionModel, model.dataset_version_id)
            if (
                draft is None
                or draft.revision != model.requested_revision
                or not _validation_is_current(draft)
            ):
                self._fail(model, "draft_changed", occurred_at)
                session.add(
                    self._audit(
                        actor_id=None,
                        action="dataset.refresh_failed",
                        dataset_id=model.dataset_id,
                        run_id=model.id,
                        occurred_at=occurred_at,
                        request_id="worker",
                        details={"error_code": "draft_changed"},
                        outcome=AuditOutcome.FAILURE,
                    )
                )
                return RefreshClaimOutcome.TERMINAL, _to_run(model)
            running = await session.scalar(
                select(DatasetRefreshRunModel.id).where(
                    DatasetRefreshRunModel.dataset_id == model.dataset_id,
                    DatasetRefreshRunModel.state == RefreshRunState.RUNNING.value,
                    DatasetRefreshRunModel.id != model.id,
                )
            )
            if running is not None:
                return RefreshClaimOutcome.DEFERRED, _to_run(model)
            model.state = RefreshRunState.RUNNING.value
            model.started_at = occurred_at
            model.attempt_count += 1
            model.updated_at = occurred_at
            session.add(
                self._audit(
                    actor_id=None,
                    action="dataset.refresh_started",
                    dataset_id=model.dataset_id,
                    run_id=model.id,
                    occurred_at=occurred_at,
                    request_id="worker",
                    details={"attempt": model.attempt_count},
                )
            )
            await session.flush()
            return RefreshClaimOutcome.CLAIMED, _to_run(model)

    async def complete(
        self,
        run_id: UUID,
        result: RefreshExecutionResult,
        occurred_at: datetime,
    ) -> None:
        async with self._session_factory() as session, session.begin():
            model = await session.get(DatasetRefreshRunModel, run_id, with_for_update=True)
            if model is None or model.state != RefreshRunState.RUNNING.value:
                return
            model.state = (
                RefreshRunState.SUCCEEDED.value
                if result.succeeded
                else RefreshRunState.FAILED.value
            )
            model.error_code = result.error_code
            model.finished_at = occurred_at
            model.updated_at = occurred_at
            session.add(
                self._audit(
                    actor_id=None,
                    action=(
                        "dataset.refresh_succeeded"
                        if result.succeeded
                        else "dataset.refresh_failed"
                    ),
                    dataset_id=model.dataset_id,
                    run_id=model.id,
                    occurred_at=occurred_at,
                    request_id="worker",
                    details={"error_code": result.error_code},
                    outcome=(AuditOutcome.SUCCESS if result.succeeded else AuditOutcome.FAILURE),
                )
            )

    @staticmethod
    def _fail(model: DatasetRefreshRunModel, error_code: str, occurred_at: datetime) -> None:
        model.state = RefreshRunState.FAILED.value
        model.error_code = error_code
        model.finished_at = occurred_at
        model.updated_at = occurred_at

    @staticmethod
    def _audit(
        *,
        actor_id: UUID | None,
        action: str,
        dataset_id: UUID,
        run_id: UUID,
        occurred_at: datetime,
        request_id: str,
        details: dict[str, Any],
        outcome: AuditOutcome = AuditOutcome.SUCCESS,
    ) -> AuditEventModel:
        return AuditEventModel(
            id=uuid4(),
            occurred_at=occurred_at,
            actor_type=(
                AuditActorType.USER.value if actor_id is not None else AuditActorType.SYSTEM.value
            ),
            actor_id=actor_id,
            action=action,
            outcome=outcome.value,
            resource_type="dataset_refresh_run",
            resource_id=run_id,
            request_id=request_id,
            details={"dataset_id": str(dataset_id), **details},
        )
