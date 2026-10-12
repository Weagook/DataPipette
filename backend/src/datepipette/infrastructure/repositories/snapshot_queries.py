"""Metadata lookup and redacted audit persistence for snapshot queries."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from datepipette.application.snapshot_queries import (
    DatasetSnapshotNotPublishedError,
    DatasetSnapshotUnavailableError,
)
from datepipette.domain.audit import AuditActorType, AuditOutcome
from datepipette.domain.datasets import DatasetValidation, dataset_definition_from_document
from datepipette.domain.refresh import RefreshRunState
from datepipette.domain.snapshot_queries import (
    ActiveSnapshotQueryContext,
    DatasetDataState,
    DatasetFreshness,
    SnapshotQueryField,
    SnapshotRefreshState,
)
from datepipette.domain.snapshots import SnapshotState
from datepipette.infrastructure.models import (
    AuditEventModel,
    DatasetModel,
    DatasetRefreshRunModel,
    DatasetSnapshotModel,
    DatasetVersionModel,
)


def query_fields_from_models(
    version: DatasetVersionModel,
    snapshot: DatasetSnapshotModel,
) -> tuple[SnapshotQueryField, ...]:
    try:
        if version.validation is None:
            raise ValueError
        definition = dataset_definition_from_document(version.definition)
        validation = DatasetValidation.from_document(version.validation)
        display_by_id = {field.id: field.display for field in definition.fields}
        snapshot_fields = snapshot.schema_document.get("fields")
        if not isinstance(snapshot_fields, list):
            raise ValueError
        storage_by_name: dict[str, str] = {}
        for item in snapshot_fields:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            storage_name = item.get("storage_name", name)
            if isinstance(name, str) and isinstance(storage_name, str):
                storage_by_name[name] = storage_name
        if any(field.name not in storage_by_name for field in validation.result_schema):
            raise ValueError
        if tuple(field.id for field in definition.fields) != tuple(
            field.field_id for field in validation.result_schema
        ):
            raise ValueError
        return tuple(
            SnapshotQueryField(
                field_id=field.field_id,
                name=field.name,
                storage_name=storage_by_name[field.name],
                data_type=field.data_type,
                nullable=field.nullable,
                visible=display_by_id[field.field_id].visible,
                label=display_by_id[field.field_id].label,
                display_format=display_by_id[field.field_id].format,
            )
            for field in validation.result_schema
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise DatasetSnapshotUnavailableError from exc


class SqlAlchemySnapshotQueryStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_freshness(self, dataset_id: UUID) -> DatasetFreshness | None:
        async with self._session_factory() as session:
            dataset = await session.get(DatasetModel, dataset_id)
            if dataset is None:
                return None
            snapshot = await self._active_snapshot(session, dataset)
            return await self._freshness(session, dataset, snapshot)

    async def get_active_context(
        self,
        dataset_id: UUID,
    ) -> ActiveSnapshotQueryContext | None:
        async with self._session_factory() as session:
            dataset = await session.get(DatasetModel, dataset_id)
            if dataset is None:
                return None
            if dataset.active_snapshot_id is None or dataset.active_version_id is None:
                raise DatasetSnapshotNotPublishedError
            row = (
                await session.execute(
                    select(DatasetSnapshotModel, DatasetVersionModel)
                    .join(
                        DatasetVersionModel,
                        DatasetVersionModel.id == DatasetSnapshotModel.dataset_version_id,
                    )
                    .where(
                        DatasetSnapshotModel.id == dataset.active_snapshot_id,
                        DatasetSnapshotModel.dataset_id == dataset.id,
                        DatasetSnapshotModel.state == SnapshotState.ACTIVE.value,
                        DatasetVersionModel.id == dataset.active_version_id,
                        DatasetVersionModel.dataset_id == dataset.id,
                    )
                )
            ).one_or_none()
            if row is None:
                raise DatasetSnapshotUnavailableError
            snapshot, version = row
            freshness = await self._freshness(session, dataset, snapshot)

        fields = query_fields_from_models(version, snapshot)
        return ActiveSnapshotQueryContext(
            dataset_id=dataset.id,
            snapshot_id=snapshot.id,
            storage_backend=snapshot.storage_backend,
            object_key=snapshot.object_key,
            activated_at=snapshot.activated_at,
            snapshot_row_count=snapshot.row_count,
            fields=fields,
            refresh_state=freshness.refresh_state,
            last_refresh_error=freshness.last_refresh_error,
            is_stale=freshness.is_stale,
            last_refresh_error_at=freshness.last_refresh_error_at,
            byte_size=snapshot.byte_size,
            checksum_sha256=snapshot.checksum_sha256,
        )

    @staticmethod
    async def _active_snapshot(
        session: AsyncSession,
        dataset: DatasetModel,
    ) -> DatasetSnapshotModel | None:
        if dataset.active_snapshot_id is None:
            return None
        snapshot = await session.get(DatasetSnapshotModel, dataset.active_snapshot_id)
        if (
            snapshot is None
            or snapshot.dataset_id != dataset.id
            or snapshot.state != SnapshotState.ACTIVE.value
        ):
            raise DatasetSnapshotUnavailableError
        return snapshot

    @staticmethod
    async def _freshness(
        session: AsyncSession,
        dataset: DatasetModel,
        snapshot: DatasetSnapshotModel | None,
    ) -> DatasetFreshness:
        running = await session.scalar(
            select(DatasetRefreshRunModel)
            .where(
                DatasetRefreshRunModel.dataset_id == dataset.id,
                DatasetRefreshRunModel.state == RefreshRunState.RUNNING.value,
            )
            .order_by(DatasetRefreshRunModel.requested_at, DatasetRefreshRunModel.id)
            .limit(1)
        )
        active_run = running or await session.scalar(
            select(DatasetRefreshRunModel)
            .where(
                DatasetRefreshRunModel.dataset_id == dataset.id,
                DatasetRefreshRunModel.state == RefreshRunState.QUEUED.value,
            )
            .order_by(DatasetRefreshRunModel.requested_at, DatasetRefreshRunModel.id)
            .limit(1)
        )
        failure_statement = (
            select(DatasetRefreshRunModel)
            .where(
                DatasetRefreshRunModel.dataset_id == dataset.id,
                DatasetRefreshRunModel.state == RefreshRunState.FAILED.value,
                DatasetRefreshRunModel.error_code.is_not(None),
                DatasetRefreshRunModel.finished_at.is_not(None),
            )
            .order_by(
                DatasetRefreshRunModel.finished_at.desc(),
                DatasetRefreshRunModel.id.desc(),
            )
            .limit(1)
        )
        if snapshot is not None:
            failure_statement = failure_statement.where(
                DatasetRefreshRunModel.finished_at >= snapshot.activated_at
            )
        latest_failure = await session.scalar(failure_statement)
        latest_error = (
            active_run
            if active_run is not None and active_run.error_code is not None
            else latest_failure
        )

        refresh_state = SnapshotRefreshState.IDLE
        if active_run is not None and active_run.state == RefreshRunState.RUNNING.value:
            refresh_state = SnapshotRefreshState.RUNNING
        elif active_run is not None:
            refresh_state = SnapshotRefreshState.QUEUED
        is_stale = snapshot is not None and latest_error is not None
        if active_run is not None:
            data_state = DatasetDataState.REFRESHING
        elif snapshot is not None and is_stale:
            data_state = DatasetDataState.STALE
        elif snapshot is not None:
            data_state = DatasetDataState.READY
        elif latest_error is not None:
            data_state = DatasetDataState.FAILED
        else:
            data_state = DatasetDataState.NOT_PUBLISHED
        return DatasetFreshness(
            dataset_id=dataset.id,
            data_state=data_state,
            snapshot_id=snapshot.id if snapshot is not None else None,
            snapshot_row_count=snapshot.row_count if snapshot is not None else None,
            last_success_at=snapshot.activated_at if snapshot is not None else None,
            refresh_state=refresh_state,
            active_refresh_run_id=active_run.id if active_run is not None else None,
            refresh_requested_at=active_run.requested_at if active_run is not None else None,
            refresh_started_at=active_run.started_at if active_run is not None else None,
            last_refresh_error=(latest_error.error_code if latest_error is not None else None),
            last_refresh_error_at=(
                (latest_error.finished_at or latest_error.updated_at)
                if latest_error is not None
                else None
            ),
            is_stale=is_stale,
        )

    async def record_query(
        self,
        *,
        context: ActiveSnapshotQueryContext,
        actor_id: UUID,
        selected_field_count: int,
        filter_count: int,
        sort_count: int,
        requested_rows: int,
        returned_rows: int,
        truncated_by_bytes: bool,
        error_code: str | None,
        request_id: str,
        occurred_at: datetime,
    ) -> None:
        async with self._session_factory() as session, session.begin():
            session.add(
                AuditEventModel(
                    id=uuid4(),
                    occurred_at=occurred_at,
                    actor_type=AuditActorType.USER.value,
                    actor_id=actor_id,
                    action=(
                        "dataset.rows_queried"
                        if error_code is None
                        else "dataset.rows_query_failed"
                    ),
                    outcome=(
                        AuditOutcome.SUCCESS.value
                        if error_code is None
                        else AuditOutcome.FAILURE.value
                    ),
                    resource_type="dataset",
                    resource_id=context.dataset_id,
                    request_id=request_id,
                    details={
                        "snapshot_id": str(context.snapshot_id),
                        "selected_field_count": selected_field_count,
                        "filter_count": filter_count,
                        "sort_count": sort_count,
                        "requested_rows": requested_rows,
                        "returned_rows": returned_rows,
                        "truncated_by_bytes": truncated_by_bytes,
                        "error_code": error_code,
                    },
                )
            )
