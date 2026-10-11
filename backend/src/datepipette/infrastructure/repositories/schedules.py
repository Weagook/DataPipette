"""Transactional refresh schedule persistence and due-run creation."""

from datetime import datetime
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from datepipette.application.schedules import (
    DueScheduleClaims,
    RefreshScheduleRevisionConflictError,
    RefreshScheduleRevisionRequiredError,
)
from datepipette.domain.audit import AuditActorType, AuditOutcome
from datepipette.domain.datasets import (
    DatasetValidation,
    DatasetValidationState,
    DatasetVersionState,
)
from datepipette.domain.refresh import RefreshRunState, RefreshTriggerType
from datepipette.domain.schedules import (
    DatasetRefreshSchedule,
    RefreshScheduleKind,
    RefreshScheduleSpec,
    next_occurrence,
)
from datepipette.infrastructure.models import (
    AuditEventModel,
    DatasetModel,
    DatasetRefreshRunModel,
    DatasetRefreshScheduleModel,
    DatasetVersionModel,
)


def _spec(model: DatasetRefreshScheduleModel) -> RefreshScheduleSpec:
    return RefreshScheduleSpec(
        kind=RefreshScheduleKind(model.kind),
        interval_minutes=model.interval_minutes,
        daily_time=model.daily_time,
    )


def _to_schedule(model: DatasetRefreshScheduleModel, timezone: str) -> DatasetRefreshSchedule:
    return DatasetRefreshSchedule(
        id=model.id,
        dataset_id=model.dataset_id,
        spec=_spec(model),
        enabled=model.enabled,
        timezone=timezone,
        next_run_at=model.next_run_at,
        last_triggered_at=model.last_triggered_at,
        revision=model.revision,
        created_by=model.created_by,
        updated_by=model.updated_by,
        created_at=model.created_at,
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


class SqlAlchemyRefreshScheduleStore:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        timezone: str,
    ) -> None:
        self._session_factory = session_factory
        self._timezone = timezone

    async def get(self, dataset_id: UUID) -> DatasetRefreshSchedule | None:
        async with self._session_factory() as session:
            model = await session.scalar(
                select(DatasetRefreshScheduleModel).where(
                    DatasetRefreshScheduleModel.dataset_id == dataset_id
                )
            )
        return _to_schedule(model, self._timezone) if model is not None else None

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
    ) -> DatasetRefreshSchedule:
        async with self._session_factory() as session, session.begin():
            dataset = await session.scalar(
                select(DatasetModel).where(DatasetModel.id == dataset_id).with_for_update()
            )
            if dataset is None:  # pragma: no cover - guarded by the application service
                raise RefreshScheduleRevisionConflictError
            model = await session.scalar(
                select(DatasetRefreshScheduleModel)
                .where(DatasetRefreshScheduleModel.dataset_id == dataset_id)
                .with_for_update()
            )
            if model is None:
                if expected_revision is not None:
                    raise RefreshScheduleRevisionConflictError
                model = DatasetRefreshScheduleModel(
                    id=schedule_id,
                    dataset_id=dataset_id,
                    kind=spec.kind.value,
                    interval_minutes=spec.interval_minutes,
                    daily_time=spec.daily_time,
                    enabled=enabled,
                    next_run_at=next_run_at,
                    last_triggered_at=None,
                    revision=1,
                    created_by=actor_id,
                    updated_by=actor_id,
                    created_at=occurred_at,
                    updated_at=occurred_at,
                )
                session.add(model)
                action = "dataset.refresh_schedule_created"
            else:
                if expected_revision is None:
                    raise RefreshScheduleRevisionRequiredError
                if model.revision != expected_revision:
                    raise RefreshScheduleRevisionConflictError
                model.kind = spec.kind.value
                model.interval_minutes = spec.interval_minutes
                model.daily_time = spec.daily_time
                model.enabled = enabled
                model.next_run_at = next_run_at
                model.revision += 1
                model.updated_by = actor_id
                model.updated_at = occurred_at
                action = "dataset.refresh_schedule_replaced"
            session.add(
                self._audit(
                    action=action,
                    actor_id=actor_id,
                    schedule_id=model.id,
                    dataset_id=dataset_id,
                    occurred_at=occurred_at,
                    request_id=request_id,
                    details={
                        "kind": spec.kind.value,
                        "enabled": enabled,
                        "revision": model.revision,
                        "timezone": timezone,
                    },
                )
            )
            await session.flush()
            return _to_schedule(model, timezone)

    async def recoverable_run_ids(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> tuple[UUID, ...]:
        async with self._session_factory() as session:
            values = await session.scalars(
                select(DatasetRefreshRunModel.id)
                .where(
                    or_(
                        and_(
                            DatasetRefreshRunModel.state == RefreshRunState.QUEUED.value,
                            DatasetRefreshRunModel.dispatched_at.is_(None),
                            or_(
                                DatasetRefreshRunModel.next_attempt_at.is_(None),
                                DatasetRefreshRunModel.next_attempt_at <= now,
                            ),
                        ),
                        and_(
                            DatasetRefreshRunModel.state == RefreshRunState.RUNNING.value,
                            or_(
                                DatasetRefreshRunModel.lease_expires_at.is_(None),
                                DatasetRefreshRunModel.lease_expires_at <= now,
                            ),
                        ),
                    )
                )
                .order_by(DatasetRefreshRunModel.requested_at, DatasetRefreshRunModel.id)
                .limit(limit)
            )
            return tuple(values)

    async def claim_due(
        self,
        *,
        now: datetime,
        timezone: ZoneInfo,
        limit: int,
    ) -> DueScheduleClaims:
        dispatch_ids: list[UUID] = []
        async with self._session_factory() as session, session.begin():
            schedules = list(
                await session.scalars(
                    select(DatasetRefreshScheduleModel)
                    .where(
                        DatasetRefreshScheduleModel.enabled.is_(True),
                        DatasetRefreshScheduleModel.next_run_at <= now,
                    )
                    .order_by(
                        DatasetRefreshScheduleModel.next_run_at,
                        DatasetRefreshScheduleModel.id,
                    )
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            for schedule in schedules:
                dataset = await session.scalar(
                    select(DatasetModel)
                    .where(DatasetModel.id == schedule.dataset_id)
                    .with_for_update()
                )
                schedule.last_triggered_at = now
                schedule.next_run_at = next_occurrence(
                    _spec(schedule),
                    after=now,
                    timezone=timezone,
                )
                schedule.updated_at = now
                if dataset is None or dataset.active_version_id is None:
                    continue
                version = await session.get(DatasetVersionModel, dataset.active_version_id)
                if (
                    version is None
                    or version.state != DatasetVersionState.PUBLISHED.value
                    or not _validation_is_current(version)
                ):
                    continue
                queued = await session.scalar(
                    select(DatasetRefreshRunModel)
                    .where(
                        DatasetRefreshRunModel.dataset_id == dataset.id,
                        DatasetRefreshRunModel.state == RefreshRunState.QUEUED.value,
                    )
                    .with_for_update()
                )
                created = queued is None
                if queued is None:
                    queued = DatasetRefreshRunModel(
                        id=uuid4(),
                        dataset_id=dataset.id,
                        dataset_version_id=version.id,
                        requested_revision=version.revision,
                        state=RefreshRunState.QUEUED.value,
                        requested_by=dataset.owner_id,
                        trigger_type=RefreshTriggerType.SCHEDULED.value,
                        schedule_id=schedule.id,
                        dispatch_attempts=0,
                        dispatched_at=None,
                        attempt_count=0,
                        error_code=None,
                        next_attempt_at=None,
                        cancel_requested_at=None,
                        cancelled_at=None,
                        lease_expires_at=None,
                        requested_at=now,
                        started_at=None,
                        finished_at=None,
                        updated_at=now,
                    )
                    session.add(queued)
                    await session.flush()
                session.add(
                    self._audit(
                        action=(
                            "dataset.refresh_schedule_triggered"
                            if created
                            else "dataset.refresh_schedule_coalesced"
                        ),
                        actor_id=None,
                        schedule_id=schedule.id,
                        dataset_id=dataset.id,
                        occurred_at=now,
                        request_id="scheduler",
                        details={
                            "run_id": str(queued.id),
                            "revision": queued.requested_revision,
                        },
                    )
                )
                if queued.dispatched_at is None and (
                    queued.next_attempt_at is None or queued.next_attempt_at <= now
                ):
                    dispatch_ids.append(queued.id)
        return DueScheduleClaims(len(schedules), tuple(dispatch_ids))

    async def record_dispatch_attempt(self, run_id: UUID, occurred_at: datetime) -> None:
        async with self._session_factory() as session, session.begin():
            model = await session.get(DatasetRefreshRunModel, run_id, with_for_update=True)
            if model is not None and (
                model.state == RefreshRunState.QUEUED.value
                or (
                    model.state == RefreshRunState.RUNNING.value
                    and (model.lease_expires_at is None or model.lease_expires_at <= occurred_at)
                )
            ):
                model.dispatch_attempts += 1
                model.updated_at = occurred_at

    async def mark_dispatched(self, run_id: UUID, occurred_at: datetime) -> None:
        async with self._session_factory() as session, session.begin():
            model = await session.get(DatasetRefreshRunModel, run_id, with_for_update=True)
            if model is not None and (
                model.state == RefreshRunState.QUEUED.value
                or (
                    model.state == RefreshRunState.RUNNING.value
                    and (model.lease_expires_at is None or model.lease_expires_at <= occurred_at)
                )
            ):
                model.dispatched_at = occurred_at
                model.updated_at = occurred_at

    @staticmethod
    def _audit(
        *,
        action: str,
        actor_id: UUID | None,
        schedule_id: UUID,
        dataset_id: UUID,
        occurred_at: datetime,
        request_id: str,
        details: dict[str, object],
    ) -> AuditEventModel:
        return AuditEventModel(
            id=uuid4(),
            occurred_at=occurred_at,
            actor_type=(
                AuditActorType.USER.value if actor_id is not None else AuditActorType.SYSTEM.value
            ),
            actor_id=actor_id,
            action=action,
            outcome=AuditOutcome.SUCCESS.value,
            resource_type="dataset_refresh_schedule",
            resource_id=schedule_id,
            request_id=request_id,
            details={"dataset_id": str(dataset_id), **details},
        )
