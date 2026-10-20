"""SQLAlchemy read repository for append-only audit events."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from datapipette.application.audit_events import AuditEventFilter
from datapipette.domain.audit import AuditActorType, AuditEvent, AuditOutcome
from datapipette.infrastructure.models import AuditEventModel


def _event(model: AuditEventModel) -> AuditEvent:
    return AuditEvent(
        id=model.id,
        occurred_at=model.occurred_at,
        actor_type=AuditActorType(model.actor_type),
        actor_id=model.actor_id,
        action=model.action,
        outcome=AuditOutcome(model.outcome),
        resource_type=model.resource_type,
        resource_id=model.resource_id,
        request_id=model.request_id,
        details=dict(model.details),
    )


class SqlAlchemyAuditEventStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def list_events(
        self,
        *,
        filters: AuditEventFilter,
        before_occurred_at: datetime | None,
        before_id: UUID | None,
        limit: int,
    ) -> tuple[AuditEvent, ...]:
        statement = select(AuditEventModel)
        if before_occurred_at is not None and before_id is not None:
            statement = statement.where(
                or_(
                    AuditEventModel.occurred_at < before_occurred_at,
                    and_(
                        AuditEventModel.occurred_at == before_occurred_at,
                        AuditEventModel.id < before_id,
                    ),
                )
            )
        if filters.actor_type is not None:
            statement = statement.where(AuditEventModel.actor_type == filters.actor_type.value)
        if filters.actor_id is not None:
            statement = statement.where(AuditEventModel.actor_id == filters.actor_id)
        if filters.action is not None:
            statement = statement.where(AuditEventModel.action == filters.action)
        if filters.outcome is not None:
            statement = statement.where(AuditEventModel.outcome == filters.outcome.value)
        if filters.resource_type is not None:
            statement = statement.where(AuditEventModel.resource_type == filters.resource_type)
        if filters.resource_id is not None:
            statement = statement.where(AuditEventModel.resource_id == filters.resource_id)
        if filters.request_id is not None:
            statement = statement.where(AuditEventModel.request_id == filters.request_id)
        if filters.occurred_from is not None:
            statement = statement.where(AuditEventModel.occurred_at >= filters.occurred_from)
        if filters.occurred_to is not None:
            statement = statement.where(AuditEventModel.occurred_at <= filters.occurred_to)
        statement = statement.order_by(
            AuditEventModel.occurred_at.desc(),
            AuditEventModel.id.desc(),
        ).limit(limit)
        async with self._session_factory() as session:
            models = (await session.scalars(statement)).all()
        return tuple(_event(model) for model in models)
