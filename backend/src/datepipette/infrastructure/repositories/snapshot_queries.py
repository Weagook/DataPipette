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
from datepipette.domain.datasets import DatasetValidation, VisualDatasetDefinition
from datepipette.domain.refresh import RefreshRunState
from datepipette.domain.snapshot_queries import (
    ActiveSnapshotQueryContext,
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


def _query_fields(
    version: DatasetVersionModel,
    snapshot: DatasetSnapshotModel,
) -> tuple[SnapshotQueryField, ...]:
    try:
        if version.validation is None:
            raise ValueError
        definition = VisualDatasetDefinition.from_document(version.definition)
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
            )
            for field in validation.result_schema
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise DatasetSnapshotUnavailableError from exc


class SqlAlchemySnapshotQueryStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

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
            latest_run = await session.scalar(
                select(DatasetRefreshRunModel)
                .where(DatasetRefreshRunModel.dataset_id == dataset.id)
                .order_by(
                    DatasetRefreshRunModel.requested_at.desc(),
                    DatasetRefreshRunModel.id.desc(),
                )
                .limit(1)
            )

        fields = _query_fields(version, snapshot)
        refresh_state = SnapshotRefreshState.IDLE
        if latest_run is not None and latest_run.state == RefreshRunState.QUEUED.value:
            refresh_state = SnapshotRefreshState.QUEUED
        elif latest_run is not None and latest_run.state == RefreshRunState.RUNNING.value:
            refresh_state = SnapshotRefreshState.RUNNING
        last_error = (
            latest_run.error_code
            if latest_run is not None and latest_run.state == RefreshRunState.FAILED.value
            else None
        )
        is_stale = bool(
            last_error is not None
            and latest_run is not None
            and latest_run.finished_at is not None
            and latest_run.finished_at >= snapshot.activated_at
        )
        return ActiveSnapshotQueryContext(
            dataset_id=dataset.id,
            snapshot_id=snapshot.id,
            storage_backend=snapshot.storage_backend,
            object_key=snapshot.object_key,
            activated_at=snapshot.activated_at,
            snapshot_row_count=snapshot.row_count,
            fields=fields,
            refresh_state=refresh_state,
            last_refresh_error=last_error,
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
