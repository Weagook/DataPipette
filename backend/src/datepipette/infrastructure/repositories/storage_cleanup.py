"""PostgreSQL coordination for analytical object lifecycle cleanup."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import exists, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from datepipette.application.storage_cleanup import CleanupObject, CleanupObjectKind
from datepipette.domain.audit import AuditActorType, AuditOutcome
from datepipette.domain.exports import ExportState
from datepipette.domain.snapshots import SnapshotState
from datepipette.infrastructure.models import (
    AuditEventModel,
    DatasetExportModel,
    DatasetModel,
    DatasetSnapshotModel,
    ObjectCleanupCursorModel,
)


class SqlAlchemyStorageCleanupStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def claim_objects(
        self,
        *,
        occurred_at: datetime,
        snapshot_before: datetime,
        export_before: datetime,
        stale_claim_before: datetime,
        limit: int,
    ) -> tuple[CleanupObject, ...]:
        async with self._session_factory() as session, session.begin():
            protected_export = exists(
                select(DatasetExportModel.id).where(
                    DatasetExportModel.snapshot_id == DatasetSnapshotModel.id,
                    or_(
                        DatasetExportModel.state.in_(
                            [ExportState.QUEUED.value, ExportState.RUNNING.value]
                        ),
                        DatasetExportModel.expires_at > occurred_at,
                    ),
                )
            )
            active_dataset = exists(
                select(DatasetModel.id).where(
                    DatasetModel.active_snapshot_id == DatasetSnapshotModel.id
                )
            )
            snapshots = list(
                (
                    await session.scalars(
                        select(DatasetSnapshotModel)
                        .where(
                            DatasetSnapshotModel.state == SnapshotState.SUPERSEDED.value,
                            DatasetSnapshotModel.superseded_at <= snapshot_before,
                            DatasetSnapshotModel.purged_at.is_(None),
                            or_(
                                DatasetSnapshotModel.cleanup_claimed_at.is_(None),
                                DatasetSnapshotModel.cleanup_claimed_at <= stale_claim_before,
                            ),
                            ~protected_export,
                            ~active_dataset,
                        )
                        .order_by(
                            DatasetSnapshotModel.superseded_at,
                            DatasetSnapshotModel.id,
                        )
                        .limit(limit)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
            )
            remaining = limit - len(snapshots)
            exports: list[DatasetExportModel] = []
            if remaining:
                exports = list(
                    (
                        await session.scalars(
                            select(DatasetExportModel)
                            .where(
                                DatasetExportModel.state == ExportState.SUCCEEDED.value,
                                DatasetExportModel.object_key.is_not(None),
                                DatasetExportModel.storage_backend.is_not(None),
                                DatasetExportModel.expires_at <= export_before,
                                DatasetExportModel.purged_at.is_(None),
                                or_(
                                    DatasetExportModel.cleanup_claimed_at.is_(None),
                                    DatasetExportModel.cleanup_claimed_at <= stale_claim_before,
                                ),
                            )
                            .order_by(DatasetExportModel.expires_at, DatasetExportModel.id)
                            .limit(remaining)
                            .with_for_update(skip_locked=True)
                        )
                    ).all()
                )
            for snapshot_model in snapshots:
                snapshot_model.cleanup_claimed_at = occurred_at
            for export_model in exports:
                export_model.cleanup_claimed_at = occurred_at
            return tuple(
                [
                    CleanupObject(
                        CleanupObjectKind.SNAPSHOT,
                        model.id,
                        model.storage_backend,
                        model.object_key,
                    )
                    for model in snapshots
                ]
                + [
                    CleanupObject(
                        CleanupObjectKind.EXPORT,
                        model.id,
                        str(model.storage_backend),
                        str(model.object_key),
                    )
                    for model in exports
                ]
            )

    async def mark_purged(self, item: CleanupObject, occurred_at: datetime) -> None:
        async with self._session_factory() as session, session.begin():
            model = await self._get_model(session, item, with_for_update=True)
            if model is None or model.purged_at is not None:
                return
            model.purged_at = occurred_at
            model.cleanup_claimed_at = None
            session.add(
                self._audit(
                    action=f"storage.{item.kind.value}_purged",
                    resource_type=item.kind.value,
                    resource_id=item.resource_id,
                    occurred_at=occurred_at,
                    details={"storage_backend": item.storage_backend},
                )
            )

    async def release_claim(self, item: CleanupObject) -> None:
        async with self._session_factory() as session, session.begin():
            model = await self._get_model(session, item, with_for_update=True)
            if model is not None and model.purged_at is None:
                model.cleanup_claimed_at = None

    async def get_cursor(self, storage_backend: str, prefix: str) -> str | None:
        async with self._session_factory() as session, session.begin():
            await session.execute(
                insert(ObjectCleanupCursorModel)
                .values(
                    storage_backend=storage_backend,
                    prefix=prefix,
                    after_key=None,
                    updated_at=datetime.now(UTC),
                )
                .on_conflict_do_nothing(index_elements=["storage_backend", "prefix"])
            )
            model = await session.get(ObjectCleanupCursorModel, (storage_backend, prefix))
            return model.after_key if model is not None else None

    async def advance_cursor(
        self,
        storage_backend: str,
        prefix: str,
        after_key: str | None,
        occurred_at: datetime,
    ) -> None:
        async with self._session_factory() as session, session.begin():
            model = await session.get(
                ObjectCleanupCursorModel,
                (storage_backend, prefix),
                with_for_update=True,
            )
            if model is None:
                model = ObjectCleanupCursorModel(
                    storage_backend=storage_backend,
                    prefix=prefix,
                    after_key=after_key,
                    updated_at=occurred_at,
                )
                session.add(model)
            else:
                model.after_key = after_key
                model.updated_at = occurred_at

    async def referenced_keys(
        self,
        storage_backend: str,
        object_keys: tuple[str, ...],
    ) -> set[str]:
        if not object_keys:
            return set()
        async with self._session_factory() as session:
            snapshot_keys = await session.scalars(
                select(DatasetSnapshotModel.object_key).where(
                    DatasetSnapshotModel.storage_backend == storage_backend,
                    DatasetSnapshotModel.object_key.in_(object_keys),
                    DatasetSnapshotModel.purged_at.is_(None),
                )
            )
            export_keys = await session.scalars(
                select(DatasetExportModel.object_key).where(
                    DatasetExportModel.storage_backend == storage_backend,
                    DatasetExportModel.object_key.in_(object_keys),
                    DatasetExportModel.purged_at.is_(None),
                )
            )
            return {str(value) for value in [*snapshot_keys, *export_keys] if value is not None}

    async def record_orphans_purged(
        self,
        storage_backend: str,
        prefix: str,
        count: int,
        occurred_at: datetime,
    ) -> None:
        async with self._session_factory() as session, session.begin():
            session.add(
                self._audit(
                    action="storage.orphans_purged",
                    resource_type="object_storage",
                    resource_id=None,
                    occurred_at=occurred_at,
                    details={
                        "storage_backend": storage_backend,
                        "prefix": prefix,
                        "count": count,
                    },
                )
            )

    @staticmethod
    async def _get_model(
        session: AsyncSession,
        item: CleanupObject,
        *,
        with_for_update: bool,
    ) -> DatasetSnapshotModel | DatasetExportModel | None:
        if item.kind is CleanupObjectKind.SNAPSHOT:
            return await session.get(
                DatasetSnapshotModel,
                item.resource_id,
                with_for_update=with_for_update,
            )
        return await session.get(
            DatasetExportModel,
            item.resource_id,
            with_for_update=with_for_update,
        )

    @staticmethod
    def _audit(
        *,
        action: str,
        resource_type: str,
        resource_id: UUID | None,
        occurred_at: datetime,
        details: dict[str, object],
    ) -> AuditEventModel:
        return AuditEventModel(
            id=uuid4(),
            occurred_at=occurred_at,
            actor_type=AuditActorType.SYSTEM.value,
            actor_id=None,
            action=action,
            outcome=AuditOutcome.SUCCESS.value,
            resource_type=resource_type,
            resource_id=resource_id,
            request_id="cleanup",
            details=details,
        )
