"""Transactional persistence and publication for dataset refresh runs."""

from copy import deepcopy
from datetime import datetime, timedelta
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
    RefreshCancelOutcome,
    RefreshClaimOutcome,
    RefreshRequestOutcome,
    RefreshRun,
    RefreshRunState,
    RefreshTriggerType,
)
from datepipette.domain.snapshots import SnapshotArtifact, SnapshotState
from datepipette.infrastructure.models import (
    AuditEventModel,
    DatasetModel,
    DatasetRefreshRequestModel,
    DatasetRefreshRunModel,
    DatasetSnapshotModel,
    DatasetVersionModel,
)


def _to_run(
    model: DatasetRefreshRunModel,
    snapshot_id: UUID | None = None,
) -> RefreshRun:
    return RefreshRun(
        id=model.id,
        dataset_id=model.dataset_id,
        dataset_version_id=model.dataset_version_id,
        requested_revision=model.requested_revision,
        state=RefreshRunState(model.state),
        requested_by=model.requested_by,
        trigger_type=RefreshTriggerType(model.trigger_type),
        schedule_id=model.schedule_id,
        dispatch_attempts=model.dispatch_attempts,
        dispatched_at=model.dispatched_at,
        attempt_count=model.attempt_count,
        policy_revision=model.policy_revision,
        error_code=model.error_code,
        next_attempt_at=model.next_attempt_at,
        cancel_requested_at=model.cancel_requested_at,
        cancelled_at=model.cancelled_at,
        lease_expires_at=model.lease_expires_at,
        snapshot_id=snapshot_id,
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
                snapshot_id = await session.scalar(
                    select(DatasetSnapshotModel.id).where(
                        DatasetSnapshotModel.refresh_run_id == existing_run.id
                    )
                )
                return RefreshRequestOutcome(
                    run=_to_run(existing_run, snapshot_id),
                    coalesced=True,
                    dispatch_required=(
                        existing_run.state == RefreshRunState.QUEUED.value
                        and existing_run.dispatched_at is None
                        and (
                            existing_run.next_attempt_at is None
                            or existing_run.next_attempt_at <= occurred_at
                        )
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
                trigger_type=RefreshTriggerType.MANUAL.value,
                schedule_id=None,
                dispatch_attempts=0,
                dispatched_at=None,
                attempt_count=0,
                policy_revision=None,
                error_code=None,
                next_attempt_at=None,
                cancel_requested_at=None,
                cancelled_at=None,
                lease_expires_at=None,
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
                dispatch_required=(
                    run.dispatched_at is None
                    and (run.next_attempt_at is None or run.next_attempt_at <= occurred_at)
                ),
            )

    async def get(self, *, dataset_id: UUID, run_id: UUID) -> RefreshRun | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(DatasetRefreshRunModel, DatasetSnapshotModel.id)
                    .outerjoin(
                        DatasetSnapshotModel,
                        DatasetSnapshotModel.refresh_run_id == DatasetRefreshRunModel.id,
                    )
                    .where(
                        DatasetRefreshRunModel.id == run_id,
                        DatasetRefreshRunModel.dataset_id == dataset_id,
                    )
                )
            ).one_or_none()
        return _to_run(*row) if row is not None else None

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

    async def cancel(
        self,
        *,
        dataset_id: UUID,
        run_id: UUID,
        actor_id: UUID,
        occurred_at: datetime,
        request_id: str,
    ) -> tuple[RefreshCancelOutcome, RefreshRun | None]:
        async with self._session_factory() as session, session.begin():
            model = await session.scalar(
                select(DatasetRefreshRunModel)
                .where(
                    DatasetRefreshRunModel.id == run_id,
                    DatasetRefreshRunModel.dataset_id == dataset_id,
                )
                .with_for_update()
            )
            if model is None:
                return RefreshCancelOutcome.NOT_FOUND, None
            if model.state == RefreshRunState.CANCELLED.value:
                return RefreshCancelOutcome.ALREADY_CANCELLED, _to_run(model)
            if model.state not in {
                RefreshRunState.QUEUED.value,
                RefreshRunState.RUNNING.value,
            }:
                return RefreshCancelOutcome.CONFLICT, _to_run(model)
            if model.cancel_requested_at is not None:
                return RefreshCancelOutcome.REQUESTED, _to_run(model)
            model.cancel_requested_at = occurred_at
            if model.state == RefreshRunState.QUEUED.value:
                self._cancel(model, occurred_at)
                action = "dataset.refresh_cancelled"
                outcome = RefreshCancelOutcome.CANCELLED
            else:
                action = "dataset.refresh_cancellation_requested"
                outcome = RefreshCancelOutcome.REQUESTED
            model.updated_at = occurred_at
            session.add(
                self._audit(
                    actor_id=actor_id,
                    action=action,
                    dataset_id=dataset_id,
                    run_id=run_id,
                    occurred_at=occurred_at,
                    request_id=request_id,
                    details={"state": model.state},
                )
            )
            await session.flush()
            return outcome, _to_run(model)

    async def is_cancel_requested(self, run_id: UUID) -> bool:
        async with self._session_factory() as session:
            value = await session.scalar(
                select(DatasetRefreshRunModel.cancel_requested_at).where(
                    DatasetRefreshRunModel.id == run_id,
                    DatasetRefreshRunModel.state == RefreshRunState.RUNNING.value,
                )
            )
            return value is not None

    async def heartbeat(
        self,
        run_id: UUID,
        occurred_at: datetime,
        lease_expires_at: datetime,
    ) -> bool:
        async with self._session_factory() as session, session.begin():
            model = await session.get(DatasetRefreshRunModel, run_id, with_for_update=True)
            if model is None or model.state != RefreshRunState.RUNNING.value:
                return True
            model.lease_expires_at = lease_expires_at
            model.updated_at = occurred_at
            return model.cancel_requested_at is not None

    async def claim(
        self,
        run_id: UUID,
        occurred_at: datetime,
        lease_expires_at: datetime | None = None,
        max_attempts: int = 3,
        policy_revision: int = 0,
    ) -> tuple[RefreshClaimOutcome, RefreshRun | None]:
        async with self._session_factory() as session, session.begin():
            model = await session.get(DatasetRefreshRunModel, run_id, with_for_update=True)
            if model is None:
                return RefreshClaimOutcome.NOT_FOUND, None
            reclaimed = model.state == RefreshRunState.RUNNING.value
            if reclaimed:
                if model.cancel_requested_at is not None:
                    self._cancel(model, occurred_at)
                    session.add(
                        self._audit(
                            actor_id=None,
                            action="dataset.refresh_cancelled",
                            dataset_id=model.dataset_id,
                            run_id=model.id,
                            occurred_at=occurred_at,
                            request_id="worker",
                            details={"attempt": model.attempt_count},
                        )
                    )
                    return RefreshClaimOutcome.TERMINAL, _to_run(model)
                if model.lease_expires_at is not None and model.lease_expires_at > occurred_at:
                    return RefreshClaimOutcome.DEFERRED, _to_run(model)
                successor = await session.scalar(
                    select(DatasetRefreshRunModel.id).where(
                        DatasetRefreshRunModel.dataset_id == model.dataset_id,
                        DatasetRefreshRunModel.state == RefreshRunState.QUEUED.value,
                        DatasetRefreshRunModel.id != model.id,
                    )
                )
                if successor is not None or model.attempt_count >= max_attempts:
                    self._fail(model, "worker_lost", occurred_at)
                    session.add(
                        self._audit(
                            actor_id=None,
                            action="dataset.refresh_failed",
                            dataset_id=model.dataset_id,
                            run_id=model.id,
                            occurred_at=occurred_at,
                            request_id="worker",
                            details={"error_code": "worker_lost"},
                            outcome=AuditOutcome.FAILURE,
                        )
                    )
                    return RefreshClaimOutcome.TERMINAL, _to_run(model)
            elif model.state != RefreshRunState.QUEUED.value:
                return RefreshClaimOutcome.TERMINAL, _to_run(model)
            if (
                not reclaimed
                and model.next_attempt_at is not None
                and model.next_attempt_at > occurred_at
            ):
                return RefreshClaimOutcome.DEFERRED, _to_run(model)
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
            if model.started_at is None:
                model.started_at = occurred_at
            model.next_attempt_at = None
            model.lease_expires_at = lease_expires_at or occurred_at + timedelta(seconds=30)
            model.attempt_count += 1
            model.policy_revision = policy_revision
            model.updated_at = occurred_at
            session.add(
                self._audit(
                    actor_id=None,
                    action=(
                        "dataset.refresh_reclaimed" if reclaimed else "dataset.refresh_started"
                    ),
                    dataset_id=model.dataset_id,
                    run_id=model.id,
                    occurred_at=occurred_at,
                    request_id="worker",
                    details={
                        "attempt": model.attempt_count,
                        "policy_revision": model.policy_revision,
                    },
                )
            )
            await session.flush()
            return RefreshClaimOutcome.CLAIMED, _to_run(model)

    async def complete(
        self,
        run_id: UUID,
        result: RefreshExecutionResult,
        occurred_at: datetime,
        retry_at: datetime | None = None,
    ) -> None:
        async with self._session_factory() as session, session.begin():
            model = await session.get(DatasetRefreshRunModel, run_id, with_for_update=True)
            if model is None or model.state != RefreshRunState.RUNNING.value:
                return
            if model.cancel_requested_at is not None or result.cancelled:
                self._cancel(model, occurred_at)
                session.add(
                    self._audit(
                        actor_id=None,
                        action="dataset.refresh_cancelled",
                        dataset_id=model.dataset_id,
                        run_id=model.id,
                        occurred_at=occurred_at,
                        request_id="worker",
                        details={
                            "attempt": model.attempt_count,
                            "policy_revision": model.policy_revision,
                        },
                    )
                )
                return
            if result.succeeded and result.snapshot is not None:
                published = await self._publish_snapshot(
                    session,
                    model,
                    result.snapshot,
                    occurred_at,
                )
                if not published:
                    result = RefreshExecutionResult(False, "draft_changed")
            elif result.succeeded:
                result = RefreshExecutionResult(False, "snapshot_write_failed")
            if not result.succeeded:
                error_code = result.error_code or "refresh_execution_failed"
                successor = await session.scalar(
                    select(DatasetRefreshRunModel.id).where(
                        DatasetRefreshRunModel.dataset_id == model.dataset_id,
                        DatasetRefreshRunModel.state == RefreshRunState.QUEUED.value,
                        DatasetRefreshRunModel.id != model.id,
                    )
                )
                if retry_at is not None and result.retryable and successor is None:
                    model.state = RefreshRunState.QUEUED.value
                    model.error_code = error_code
                    model.dispatched_at = None
                    model.next_attempt_at = retry_at
                    model.lease_expires_at = None
                    model.finished_at = None
                    model.updated_at = occurred_at
                    session.add(
                        self._audit(
                            actor_id=None,
                            action="dataset.refresh_retry_scheduled",
                            dataset_id=model.dataset_id,
                            run_id=model.id,
                            occurred_at=occurred_at,
                            request_id="worker",
                            details={
                                "attempt": model.attempt_count,
                                "error_code": error_code,
                                "next_attempt_at": retry_at.isoformat(),
                                "policy_revision": model.policy_revision,
                            },
                        )
                    )
                    return
                self._fail(model, error_code, occurred_at)
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
                    details={
                        "error_code": result.error_code,
                        "snapshot_id": (
                            str(result.snapshot.id)
                            if result.succeeded and result.snapshot is not None
                            else None
                        ),
                        "row_count": (
                            result.snapshot.row_count
                            if result.succeeded and result.snapshot is not None
                            else None
                        ),
                        "policy_revision": model.policy_revision,
                    },
                    outcome=(AuditOutcome.SUCCESS if result.succeeded else AuditOutcome.FAILURE),
                )
            )

    async def _publish_snapshot(
        self,
        session: AsyncSession,
        run: DatasetRefreshRunModel,
        artifact: SnapshotArtifact,
        occurred_at: datetime,
    ) -> bool:
        dataset = await session.get(DatasetModel, run.dataset_id, with_for_update=True)
        version = await session.get(
            DatasetVersionModel,
            run.dataset_version_id,
            with_for_update=True,
        )
        if dataset is None or version is None or version.revision != run.requested_revision:
            return False
        if version.state == DatasetVersionState.DRAFT.value:
            if not _validation_is_current(version):
                return False
            if dataset.active_version_id is not None:
                active_version = await session.get(
                    DatasetVersionModel,
                    dataset.active_version_id,
                    with_for_update=True,
                )
                if active_version is not None and active_version.id != version.id:
                    active_version.state = DatasetVersionState.SUPERSEDED.value
                    active_version.updated_at = occurred_at
            version.state = DatasetVersionState.PUBLISHED.value
            version.updated_at = occurred_at
            await session.flush()
            session.add(
                DatasetVersionModel(
                    id=uuid4(),
                    dataset_id=dataset.id,
                    state=DatasetVersionState.DRAFT.value,
                    revision=version.revision,
                    definition=deepcopy(version.definition),
                    validation=deepcopy(version.validation),
                    created_by=run.requested_by,
                    created_at=occurred_at,
                    updated_at=occurred_at,
                )
            )
        elif not (
            version.state == DatasetVersionState.PUBLISHED.value
            and dataset.active_version_id == version.id
        ):
            return False

        previous_snapshot = await session.scalar(
            select(DatasetSnapshotModel)
            .where(
                DatasetSnapshotModel.dataset_id == dataset.id,
                DatasetSnapshotModel.state == SnapshotState.ACTIVE.value,
            )
            .with_for_update()
        )
        if previous_snapshot is not None:
            previous_snapshot.state = SnapshotState.SUPERSEDED.value
            previous_snapshot.superseded_at = occurred_at
            await session.flush()
        snapshot = DatasetSnapshotModel(
            id=artifact.id,
            dataset_id=dataset.id,
            dataset_version_id=version.id,
            refresh_run_id=run.id,
            state=SnapshotState.ACTIVE.value,
            storage_backend=artifact.storage_backend,
            object_key=artifact.object_key,
            row_count=artifact.row_count,
            byte_size=artifact.byte_size,
            checksum_sha256=artifact.checksum_sha256,
            schema_document=artifact.schema_document,
            created_at=occurred_at,
            activated_at=occurred_at,
            superseded_at=None,
        )
        session.add(snapshot)
        await session.flush()
        dataset.active_version_id = version.id
        dataset.active_snapshot_id = snapshot.id
        dataset.updated_at = occurred_at
        run.state = RefreshRunState.SUCCEEDED.value
        run.error_code = None
        run.next_attempt_at = None
        run.lease_expires_at = None
        run.finished_at = occurred_at
        run.updated_at = occurred_at
        return True

    @staticmethod
    def _fail(model: DatasetRefreshRunModel, error_code: str, occurred_at: datetime) -> None:
        model.state = RefreshRunState.FAILED.value
        model.error_code = error_code
        model.next_attempt_at = None
        model.lease_expires_at = None
        model.finished_at = occurred_at
        model.updated_at = occurred_at

    @staticmethod
    def _cancel(model: DatasetRefreshRunModel, occurred_at: datetime) -> None:
        model.state = RefreshRunState.CANCELLED.value
        model.error_code = None
        model.dispatched_at = None
        model.next_attempt_at = None
        model.lease_expires_at = None
        model.cancel_requested_at = model.cancel_requested_at or occurred_at
        model.cancelled_at = occurred_at
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
