"""Transactional persistence for durable dataset exports."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from datepipette.application.exports import (
    EXPORT_RETENTION,
    ExportExecutionResult,
    export_query_from_document,
)
from datepipette.domain.audit import AuditActorType, AuditOutcome
from datepipette.domain.exports import (
    ExportClaimOutcome,
    ExportFormat,
    ExportJob,
    ExportRequestOutcome,
    ExportState,
)
from datepipette.domain.snapshot_queries import ActiveSnapshotQueryContext, SnapshotRefreshState
from datepipette.infrastructure.models import (
    AuditEventModel,
    DatasetExportModel,
    DatasetExportRequestModel,
    DatasetModel,
    DatasetSnapshotModel,
    DatasetVersionModel,
)
from datepipette.infrastructure.repositories.snapshot_queries import (
    SqlAlchemySnapshotQueryStore,
    query_fields_from_models,
)


def _to_job(model: DatasetExportModel) -> ExportJob:
    return ExportJob(
        id=model.id,
        dataset_id=model.dataset_id,
        snapshot_id=model.snapshot_id,
        requested_by=model.requested_by,
        format=ExportFormat(model.format),
        state=ExportState(model.state),
        query=export_query_from_document(model.query_document),
        dispatch_attempts=model.dispatch_attempts,
        dispatched_at=model.dispatched_at,
        attempt_count=model.attempt_count,
        policy_revision=model.policy_revision,
        object_key=model.object_key,
        storage_backend=model.storage_backend,
        row_count=model.row_count,
        byte_size=model.byte_size,
        checksum_sha256=model.checksum_sha256,
        content_type=model.content_type,
        error_code=model.error_code,
        requested_at=model.requested_at,
        started_at=model.started_at,
        finished_at=model.finished_at,
        expires_at=model.expires_at,
        updated_at=model.updated_at,
    )


class SqlAlchemyExportStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._snapshot_store = SqlAlchemySnapshotQueryStore(session_factory)

    async def get_active_context(self, dataset_id: UUID) -> ActiveSnapshotQueryContext | None:
        return await self._snapshot_store.get_active_context(dataset_id)

    async def request(
        self,
        *,
        export_id: UUID,
        dataset_id: UUID,
        snapshot_id: UUID,
        actor_id: UUID,
        format: ExportFormat,
        query_document: dict[str, Any],
        idempotency_key_hash: str,
        request_id: str,
        occurred_at: datetime,
        expires_at: datetime,
    ) -> ExportRequestOutcome | None:
        async with self._session_factory() as session, session.begin():
            existing = await session.scalar(
                select(DatasetExportRequestModel).where(
                    DatasetExportRequestModel.dataset_id == dataset_id,
                    DatasetExportRequestModel.actor_id == actor_id,
                    DatasetExportRequestModel.idempotency_key_hash == idempotency_key_hash,
                )
            )
            if existing is not None:
                model = await session.get(DatasetExportModel, existing.export_id)
                if model is None:  # pragma: no cover
                    raise RuntimeError("Export request points to a missing job")
                return ExportRequestOutcome(
                    _to_job(model),
                    replayed=True,
                    dispatch_required=(
                        model.state == ExportState.QUEUED.value and model.dispatched_at is None
                    ),
                )
            dataset = await session.get(DatasetModel, dataset_id, with_for_update=True)
            if dataset is None or dataset.active_snapshot_id != snapshot_id:
                return None
            snapshot = await session.get(DatasetSnapshotModel, snapshot_id)
            if snapshot is None or snapshot.dataset_id != dataset_id:
                return None
            model = DatasetExportModel(
                id=export_id,
                dataset_id=dataset_id,
                snapshot_id=snapshot_id,
                requested_by=actor_id,
                format=format.value,
                state=ExportState.QUEUED.value,
                query_document=query_document,
                dispatch_attempts=0,
                dispatched_at=None,
                attempt_count=0,
                policy_revision=None,
                object_key=None,
                storage_backend=None,
                row_count=None,
                byte_size=None,
                checksum_sha256=None,
                content_type=None,
                error_code=None,
                request_id=request_id,
                requested_at=occurred_at,
                started_at=None,
                finished_at=None,
                expires_at=expires_at,
                updated_at=occurred_at,
            )
            session.add(model)
            await session.flush()
            session.add(
                DatasetExportRequestModel(
                    id=uuid4(),
                    dataset_id=dataset_id,
                    actor_id=actor_id,
                    idempotency_key_hash=idempotency_key_hash,
                    export_id=export_id,
                    requested_at=occurred_at,
                )
            )
            session.add(
                self._audit(
                    actor_id=actor_id,
                    action="dataset.export_requested",
                    dataset_id=dataset_id,
                    export_id=export_id,
                    occurred_at=occurred_at,
                    request_id=request_id,
                    details={
                        "snapshot_id": str(snapshot_id),
                        "format": format.value,
                        "selected_field_count": len(query_document.get("fields", [])),
                        "filter_count": len(query_document.get("filters", [])),
                        "sort_count": len(query_document.get("sorts", [])),
                    },
                )
            )
            return ExportRequestOutcome(_to_job(model), False, True)

    async def get(self, *, dataset_id: UUID, export_id: UUID) -> ExportJob | None:
        async with self._session_factory() as session:
            model = await session.scalar(
                select(DatasetExportModel).where(
                    DatasetExportModel.id == export_id,
                    DatasetExportModel.dataset_id == dataset_id,
                )
            )
        return _to_job(model) if model is not None else None

    async def get_by_id(self, export_id: UUID) -> ExportJob | None:
        async with self._session_factory() as session:
            model = await session.get(DatasetExportModel, export_id)
        return _to_job(model) if model is not None else None

    async def list(
        self, *, dataset_id: UUID, requested_by: UUID | None, limit: int
    ) -> tuple[ExportJob, ...]:
        async with self._session_factory() as session:
            statement = select(DatasetExportModel).where(
                DatasetExportModel.dataset_id == dataset_id
            )
            if requested_by is not None:
                statement = statement.where(DatasetExportModel.requested_by == requested_by)
            models = (
                await session.scalars(
                    statement.order_by(
                        DatasetExportModel.requested_at.desc(), DatasetExportModel.id.desc()
                    ).limit(limit)
                )
            ).all()
        return tuple(_to_job(model) for model in models)

    async def record_dispatch_attempt(self, export_id: UUID, occurred_at: datetime) -> None:
        async with self._session_factory() as session, session.begin():
            model = await session.get(DatasetExportModel, export_id, with_for_update=True)
            if model is not None and model.state == ExportState.QUEUED.value:
                model.dispatch_attempts += 1
                model.updated_at = occurred_at

    async def mark_dispatched(self, export_id: UUID, occurred_at: datetime) -> None:
        async with self._session_factory() as session, session.begin():
            model = await session.get(DatasetExportModel, export_id, with_for_update=True)
            if model is not None and model.state == ExportState.QUEUED.value:
                model.dispatched_at = occurred_at
                model.updated_at = occurred_at

    async def claim(
        self, export_id: UUID, occurred_at: datetime, policy_revision: int
    ) -> tuple[ExportClaimOutcome, ExportJob | None]:
        async with self._session_factory() as session, session.begin():
            model = await session.get(DatasetExportModel, export_id, with_for_update=True)
            if model is None:
                return ExportClaimOutcome.NOT_FOUND, None
            if model.state != ExportState.QUEUED.value:
                return ExportClaimOutcome.TERMINAL, _to_job(model)
            if occurred_at >= model.expires_at:
                self._fail(model, "export_expired", occurred_at)
                return ExportClaimOutcome.TERMINAL, _to_job(model)
            model.state = ExportState.RUNNING.value
            model.started_at = occurred_at
            model.attempt_count += 1
            model.policy_revision = policy_revision
            model.updated_at = occurred_at
            session.add(
                self._audit(
                    actor_id=None,
                    action="dataset.export_started",
                    dataset_id=model.dataset_id,
                    export_id=model.id,
                    occurred_at=occurred_at,
                    request_id=model.request_id,
                    details={
                        "attempt": model.attempt_count,
                        "policy_revision": policy_revision,
                    },
                )
            )
            await session.flush()
            return ExportClaimOutcome.CLAIMED, _to_job(model)

    async def complete(
        self, export_id: UUID, result: ExportExecutionResult, occurred_at: datetime
    ) -> None:
        async with self._session_factory() as session, session.begin():
            model = await session.get(DatasetExportModel, export_id, with_for_update=True)
            if model is None or model.state != ExportState.RUNNING.value:
                return
            if result.succeeded and result.artifact is not None:
                artifact = result.artifact
                model.state = ExportState.SUCCEEDED.value
                model.object_key = artifact.object_key
                model.storage_backend = artifact.storage_backend
                model.row_count = artifact.row_count
                model.byte_size = artifact.byte_size
                model.checksum_sha256 = artifact.checksum_sha256
                model.content_type = artifact.content_type
                model.error_code = None
                model.expires_at = occurred_at + EXPORT_RETENTION
            else:
                self._fail(model, result.error_code or "export_generation_failed", occurred_at)
            model.finished_at = occurred_at
            model.updated_at = occurred_at
            session.add(
                self._audit(
                    actor_id=None,
                    action=(
                        "dataset.export_succeeded"
                        if model.state == ExportState.SUCCEEDED.value
                        else "dataset.export_failed"
                    ),
                    dataset_id=model.dataset_id,
                    export_id=model.id,
                    occurred_at=occurred_at,
                    request_id=model.request_id,
                    details={
                        "storage_backend": model.storage_backend,
                        "row_count": model.row_count,
                        "byte_size": model.byte_size,
                        "error_code": model.error_code,
                        "policy_revision": model.policy_revision,
                    },
                    outcome=(
                        AuditOutcome.SUCCESS
                        if model.state == ExportState.SUCCEEDED.value
                        else AuditOutcome.FAILURE
                    ),
                )
            )

    async def get_context(self, job: ExportJob) -> ActiveSnapshotQueryContext | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(DatasetSnapshotModel, DatasetVersionModel)
                    .join(
                        DatasetVersionModel,
                        DatasetVersionModel.id == DatasetSnapshotModel.dataset_version_id,
                    )
                    .where(
                        DatasetSnapshotModel.id == job.snapshot_id,
                        DatasetSnapshotModel.dataset_id == job.dataset_id,
                    )
                )
            ).one_or_none()
        if row is None:
            return None
        snapshot, version = row
        return ActiveSnapshotQueryContext(
            dataset_id=job.dataset_id,
            snapshot_id=snapshot.id,
            storage_backend=snapshot.storage_backend,
            object_key=snapshot.object_key,
            activated_at=snapshot.activated_at,
            snapshot_row_count=snapshot.row_count,
            fields=query_fields_from_models(version, snapshot),
            refresh_state=SnapshotRefreshState.IDLE,
            last_refresh_error=None,
            is_stale=False,
            byte_size=snapshot.byte_size,
            checksum_sha256=snapshot.checksum_sha256,
        )

    @staticmethod
    def _fail(model: DatasetExportModel, error_code: str, occurred_at: datetime) -> None:
        model.state = ExportState.FAILED.value
        model.error_code = error_code
        model.finished_at = occurred_at
        model.updated_at = occurred_at

    @staticmethod
    def _audit(
        *,
        actor_id: UUID | None,
        action: str,
        dataset_id: UUID,
        export_id: UUID,
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
            resource_type="dataset_export",
            resource_id=export_id,
            request_id=request_id,
            details={"dataset_id": str(dataset_id), **details},
        )
