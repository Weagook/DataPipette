"""Transactional persistence for visual dataset drafts."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from datepipette.application.datasets import (
    DatasetAlreadyExistsError,
    DatasetCreate,
    DatasetRevisionConflictError,
)
from datepipette.domain.audit import AuditActorType, AuditOutcome
from datepipette.domain.datasets import (
    Dataset,
    DatasetDraft,
    DatasetVersionState,
    VisualDatasetDefinition,
)
from datepipette.infrastructure.models import (
    AuditEventModel,
    DatasetModel,
    DatasetVersionModel,
    PostgresConnectionModel,
)


def _to_dataset(model: DatasetModel, draft_revision: int) -> Dataset:
    return Dataset(
        id=model.id,
        name=model.name,
        owner_id=model.owner_id,
        connection_id=model.connection_id,
        active_version_id=model.active_version_id,
        draft_revision=draft_revision,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _to_draft(model: DatasetVersionModel) -> DatasetDraft:
    return DatasetDraft(
        id=model.id,
        dataset_id=model.dataset_id,
        revision=model.revision,
        definition=VisualDatasetDefinition.from_document(model.definition),
        created_by=model.created_by,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _summary(definition: dict[str, object]) -> dict[str, object]:
    fields = definition.get("fields")
    return {
        "schema_version": definition.get("schema_version"),
        "field_count": len(fields) if isinstance(fields, list) else 0,
    }


class SqlAlchemyDatasetStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def connection_exists(self, connection_id: UUID) -> bool:
        async with self._session_factory() as session:
            identifier = await session.scalar(
                select(PostgresConnectionModel.id).where(
                    PostgresConnectionModel.id == connection_id
                )
            )
        return identifier is not None

    async def create(
        self,
        *,
        dataset_id: UUID,
        draft_id: UUID,
        actor_id: UUID,
        value: DatasetCreate,
        definition: dict[str, object],
        occurred_at: datetime,
        request_id: str,
    ) -> tuple[Dataset, DatasetDraft]:
        dataset_model = DatasetModel(
            id=dataset_id,
            name=value.name,
            owner_id=actor_id,
            connection_id=value.connection_id,
            active_version_id=None,
            created_at=occurred_at,
            updated_at=occurred_at,
        )
        draft_model = DatasetVersionModel(
            id=draft_id,
            dataset_id=dataset_id,
            state=DatasetVersionState.DRAFT.value,
            revision=1,
            definition=definition,
            created_by=actor_id,
            created_at=occurred_at,
            updated_at=occurred_at,
        )
        try:
            async with self._session_factory() as session, session.begin():
                session.add_all((dataset_model, draft_model))
                session.add(
                    self._audit(
                        actor_id=actor_id,
                        action="dataset.created",
                        resource_id=dataset_id,
                        occurred_at=occurred_at,
                        request_id=request_id,
                        details={"revision": 1, **_summary(definition)},
                    )
                )
                await session.flush()
        except IntegrityError as exc:
            self._translate_integrity_error(exc)
            raise
        return _to_dataset(dataset_model, 1), _to_draft(draft_model)

    async def list(
        self,
        *,
        after_created_at: datetime | None,
        after_id: UUID | None,
        limit: int,
    ) -> list[Dataset]:
        statement = (
            select(DatasetModel, DatasetVersionModel.revision)
            .join(
                DatasetVersionModel,
                and_(
                    DatasetVersionModel.dataset_id == DatasetModel.id,
                    DatasetVersionModel.state == DatasetVersionState.DRAFT.value,
                ),
            )
            .order_by(DatasetModel.created_at, DatasetModel.id)
            .limit(limit)
        )
        if after_created_at is not None and after_id is not None:
            statement = statement.where(
                or_(
                    DatasetModel.created_at > after_created_at,
                    and_(
                        DatasetModel.created_at == after_created_at,
                        DatasetModel.id > after_id,
                    ),
                )
            )
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).all()
        return [_to_dataset(model, revision) for model, revision in rows]

    async def get(self, dataset_id: UUID) -> Dataset | None:
        statement = (
            select(DatasetModel, DatasetVersionModel.revision)
            .join(
                DatasetVersionModel,
                and_(
                    DatasetVersionModel.dataset_id == DatasetModel.id,
                    DatasetVersionModel.state == DatasetVersionState.DRAFT.value,
                ),
            )
            .where(DatasetModel.id == dataset_id)
        )
        async with self._session_factory() as session:
            row = (await session.execute(statement)).one_or_none()
        return _to_dataset(*row) if row is not None else None

    async def get_draft(self, dataset_id: UUID) -> DatasetDraft | None:
        statement = select(DatasetVersionModel).where(
            DatasetVersionModel.dataset_id == dataset_id,
            DatasetVersionModel.state == DatasetVersionState.DRAFT.value,
        )
        async with self._session_factory() as session:
            model = await session.scalar(statement)
        return _to_draft(model) if model is not None else None

    async def rename(
        self,
        *,
        dataset_id: UUID,
        actor_id: UUID,
        name: str,
        occurred_at: datetime,
        request_id: str,
    ) -> Dataset | None:
        try:
            async with self._session_factory() as session, session.begin():
                model = await session.get(DatasetModel, dataset_id)
                if model is None:
                    return None
                if model.name != name:
                    model.name = name
                    model.updated_at = occurred_at
                    session.add(
                        self._audit(
                            actor_id=actor_id,
                            action="dataset.renamed",
                            resource_id=dataset_id,
                            occurred_at=occurred_at,
                            request_id=request_id,
                            details={},
                        )
                    )
                    await session.flush()
                revision = await session.scalar(
                    select(DatasetVersionModel.revision).where(
                        DatasetVersionModel.dataset_id == dataset_id,
                        DatasetVersionModel.state == DatasetVersionState.DRAFT.value,
                    )
                )
                if revision is None:
                    return None
                return _to_dataset(model, revision)
        except IntegrityError as exc:
            self._translate_integrity_error(exc)
            raise

    async def replace_draft(
        self,
        *,
        dataset_id: UUID,
        actor_id: UUID,
        expected_revision: int,
        definition: dict[str, object],
        occurred_at: datetime,
        request_id: str,
    ) -> DatasetDraft | None:
        async with self._session_factory() as session, session.begin():
            model = await session.scalar(
                select(DatasetVersionModel)
                .where(
                    DatasetVersionModel.dataset_id == dataset_id,
                    DatasetVersionModel.state == DatasetVersionState.DRAFT.value,
                )
                .with_for_update()
            )
            if model is None:
                return None
            if model.revision != expected_revision:
                raise DatasetRevisionConflictError
            if model.definition != definition:
                model.definition = definition
                model.revision += 1
                model.updated_at = occurred_at
                dataset_model = await session.get(DatasetModel, dataset_id)
                if dataset_model is None:
                    return None
                dataset_model.updated_at = occurred_at
                session.add(
                    self._audit(
                        actor_id=actor_id,
                        action="dataset.draft_replaced",
                        resource_id=dataset_id,
                        occurred_at=occurred_at,
                        request_id=request_id,
                        details={"revision": model.revision, **_summary(definition)},
                    )
                )
                await session.flush()
            return _to_draft(model)

    @staticmethod
    def _translate_integrity_error(exc: IntegrityError) -> None:
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint == "uq_datasets_owner_name_normalized":
            raise DatasetAlreadyExistsError from exc

    @staticmethod
    def _audit(
        *,
        actor_id: UUID,
        action: str,
        resource_id: UUID,
        occurred_at: datetime,
        request_id: str,
        details: dict[str, Any],
    ) -> AuditEventModel:
        return AuditEventModel(
            id=uuid4(),
            occurred_at=occurred_at,
            actor_type=AuditActorType.USER.value,
            actor_id=actor_id,
            action=action,
            outcome=AuditOutcome.SUCCESS.value,
            resource_type="dataset",
            resource_id=resource_id,
            request_id=request_id,
            details=details,
        )
