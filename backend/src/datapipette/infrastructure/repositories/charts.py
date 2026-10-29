"""Transactional persistence for private charts and their field dependencies."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from datapipette.application.charts import (
    ChartAlreadyExistsError,
    ChartCreate,
    ChartRevisionConflictError,
)
from datapipette.domain.audit import AuditActorType, AuditOutcome
from datapipette.domain.charts import Chart, ChartDefinition
from datapipette.domain.identity import AccountType
from datapipette.domain.snapshot_queries import ActiveSnapshotQueryContext
from datapipette.infrastructure.models import (
    AuditEventModel,
    ChartFieldDependencyModel,
    ChartModel,
)


def _to_chart(model: ChartModel) -> Chart:
    return Chart(
        id=model.id,
        dataset_id=model.dataset_id,
        owner_id=model.owner_id,
        name=model.name,
        revision=model.revision,
        definition=ChartDefinition.from_document(model.definition),
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


class SqlAlchemyChartStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(
        self,
        *,
        chart_id: UUID,
        actor_id: UUID,
        value: ChartCreate,
        definition: dict[str, object],
        field_ids: frozenset[UUID],
        occurred_at: datetime,
        request_id: str,
    ) -> Chart:
        model = ChartModel(
            id=chart_id,
            dataset_id=value.dataset_id,
            owner_id=actor_id,
            name=value.name,
            revision=1,
            definition=definition,
            created_at=occurred_at,
            updated_at=occurred_at,
        )
        try:
            async with self._session_factory() as session, session.begin():
                session.add(model)
                await session.flush()
                session.add_all(
                    self._dependencies(
                        chart_id=chart_id,
                        dataset_id=value.dataset_id,
                        field_ids=field_ids,
                    )
                )
                session.add(
                    self._audit(
                        actor_id=actor_id,
                        action="chart.created",
                        resource_id=chart_id,
                        occurred_at=occurred_at,
                        request_id=request_id,
                        details={
                            "dataset_id": str(value.dataset_id),
                            "revision": 1,
                            "field_count": len(field_ids),
                        },
                    )
                )
                await session.flush()
        except IntegrityError as exc:
            self._translate_integrity_error(exc)
            raise
        return _to_chart(model)

    async def list(
        self,
        *,
        owner_id: UUID | None,
        after_created_at: datetime | None,
        after_id: UUID | None,
        limit: int,
    ) -> tuple[Chart, ...]:
        statement = select(ChartModel).order_by(ChartModel.created_at, ChartModel.id).limit(limit)
        if owner_id is not None:
            statement = statement.where(ChartModel.owner_id == owner_id)
        if after_created_at is not None and after_id is not None:
            statement = statement.where(
                or_(
                    ChartModel.created_at > after_created_at,
                    and_(
                        ChartModel.created_at == after_created_at,
                        ChartModel.id > after_id,
                    ),
                )
            )
        async with self._session_factory() as session:
            models = tuple(await session.scalars(statement))
        return tuple(_to_chart(model) for model in models)

    async def get(self, chart_id: UUID) -> Chart | None:
        async with self._session_factory() as session:
            model = await session.get(ChartModel, chart_id)
        return _to_chart(model) if model is not None else None

    async def update(
        self,
        *,
        chart_id: UUID,
        actor_id: UUID,
        expected_revision: int,
        name: str,
        definition: dict[str, object],
        field_ids: frozenset[UUID],
        occurred_at: datetime,
        request_id: str,
    ) -> Chart | None:
        try:
            async with self._session_factory() as session, session.begin():
                model = await session.scalar(
                    select(ChartModel).where(ChartModel.id == chart_id).with_for_update()
                )
                if model is None:
                    return None
                if model.revision != expected_revision:
                    raise ChartRevisionConflictError
                if model.name != name or model.definition != definition:
                    model.name = name
                    model.definition = definition
                    model.revision += 1
                    model.updated_at = occurred_at
                    await session.execute(
                        delete(ChartFieldDependencyModel).where(
                            ChartFieldDependencyModel.chart_id == chart_id
                        )
                    )
                    session.add_all(
                        self._dependencies(
                            chart_id=chart_id,
                            dataset_id=model.dataset_id,
                            field_ids=field_ids,
                        )
                    )
                    session.add(
                        self._audit(
                            actor_id=actor_id,
                            action="chart.updated",
                            resource_id=chart_id,
                            occurred_at=occurred_at,
                            request_id=request_id,
                            details={
                                "dataset_id": str(model.dataset_id),
                                "revision": model.revision,
                                "field_count": len(field_ids),
                            },
                        )
                    )
                    await session.flush()
                return _to_chart(model)
        except IntegrityError as exc:
            self._translate_integrity_error(exc)
            raise

    async def record_preview(
        self,
        *,
        dataset_id: UUID,
        context: ActiveSnapshotQueryContext,
        actor_id: UUID,
        policy_revision: int,
        returned_rows: int,
        truncated_by_bytes: bool,
        error_code: str | None,
        request_id: str,
        occurred_at: datetime,
    ) -> None:
        async with self._session_factory() as session, session.begin():
            session.add(
                self._audit(
                    actor_id=actor_id,
                    action="chart.previewed" if error_code is None else "chart.preview_failed",
                    resource_type="dataset",
                    resource_id=dataset_id,
                    occurred_at=occurred_at,
                    request_id=request_id,
                    outcome=(AuditOutcome.SUCCESS if error_code is None else AuditOutcome.FAILURE),
                    details={
                        "snapshot_id": str(context.snapshot_id),
                        "policy_revision": policy_revision,
                        "returned_rows": returned_rows,
                        "truncated_by_bytes": truncated_by_bytes,
                        "error_code": error_code,
                    },
                )
            )

    async def record_query(
        self,
        *,
        chart: Chart,
        context: ActiveSnapshotQueryContext,
        actor_id: UUID,
        actor_type: AccountType,
        policy_revision: int,
        returned_rows: int,
        truncated_by_bytes: bool,
        error_code: str | None,
        request_id: str,
        occurred_at: datetime,
    ) -> None:
        async with self._session_factory() as session, session.begin():
            session.add(
                self._audit(
                    actor_id=actor_id,
                    actor_type=(
                        AuditActorType.SERVICE_ACCOUNT
                        if actor_type is AccountType.SERVICE
                        else AuditActorType.USER
                    ),
                    action="chart.queried" if error_code is None else "chart.query_failed",
                    resource_id=chart.id,
                    occurred_at=occurred_at,
                    request_id=request_id,
                    outcome=(AuditOutcome.SUCCESS if error_code is None else AuditOutcome.FAILURE),
                    details={
                        "dataset_id": str(chart.dataset_id),
                        "snapshot_id": str(context.snapshot_id),
                        "chart_revision": chart.revision,
                        "policy_revision": policy_revision,
                        "returned_rows": returned_rows,
                        "truncated_by_bytes": truncated_by_bytes,
                        "error_code": error_code,
                    },
                )
            )

    @staticmethod
    def _dependencies(
        *,
        chart_id: UUID,
        dataset_id: UUID,
        field_ids: frozenset[UUID],
    ) -> tuple[ChartFieldDependencyModel, ...]:
        return tuple(
            ChartFieldDependencyModel(
                id=uuid4(),
                chart_id=chart_id,
                dataset_id=dataset_id,
                field_id=field_id,
            )
            for field_id in sorted(field_ids, key=str)
        )

    @staticmethod
    def _audit(
        *,
        actor_id: UUID,
        action: str,
        resource_id: UUID,
        occurred_at: datetime,
        request_id: str,
        details: dict[str, object],
        outcome: AuditOutcome = AuditOutcome.SUCCESS,
        actor_type: AuditActorType = AuditActorType.USER,
        resource_type: str = "chart",
    ) -> AuditEventModel:
        return AuditEventModel(
            id=uuid4(),
            occurred_at=occurred_at,
            actor_type=actor_type.value,
            actor_id=actor_id,
            action=action,
            outcome=outcome.value,
            resource_type=resource_type,
            resource_id=resource_id,
            request_id=request_id,
            details=details,
        )

    @staticmethod
    def _translate_integrity_error(exc: IntegrityError) -> None:
        if "uq_charts_owner_name_normalized" in str(exc.orig):
            raise ChartAlreadyExistsError from exc
