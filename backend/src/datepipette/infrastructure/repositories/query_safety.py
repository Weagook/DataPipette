"""Transactional Query Safety policy persistence."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from datepipette.application.query_safety import (
    ManagedQuerySafetyPolicy,
    QuerySafetyPolicyRevisionConflictError,
)
from datepipette.domain.audit import AuditActorType, AuditOutcome
from datepipette.domain.datasets import QuerySafetyPolicy
from datepipette.infrastructure.models import AuditEventModel, QuerySafetyPolicyModel


def _policy(model: QuerySafetyPolicyModel) -> QuerySafetyPolicy:
    return QuerySafetyPolicy(
        estimated_rows_warning=model.estimated_rows_warning,
        estimated_rows_block=model.estimated_rows_block,
        total_cost_warning=model.total_cost_warning,
        total_cost_block=model.total_cost_block,
        join_count_warning=model.join_count_warning,
        join_count_block=model.join_count_block,
        sequential_scan_rows_warning=model.sequential_scan_rows_warning,
        sort_rows_warning=model.sort_rows_warning,
        stale_statistics_days=model.stale_statistics_days,
    )


def _managed(model: QuerySafetyPolicyModel) -> ManagedQuerySafetyPolicy:
    return ManagedQuerySafetyPolicy(
        _policy(model), model.revision, model.updated_at, model.updated_by
    )


class SqlAlchemyQuerySafetyPolicyStore:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        default_policy: QuerySafetyPolicy,
    ) -> None:
        self._session_factory = session_factory
        self._default_policy = default_policy

    async def get(self) -> ManagedQuerySafetyPolicy:
        async with self._session_factory() as session:
            model = await session.get(QuerySafetyPolicyModel, 1)
        if model is None:
            return ManagedQuerySafetyPolicy(self._default_policy, 0, None, None)
        return _managed(model)

    async def replace(
        self,
        *,
        policy: QuerySafetyPolicy,
        expected_revision: int,
        actor_id: UUID,
        occurred_at: datetime,
        request_id: str,
    ) -> ManagedQuerySafetyPolicy:
        async with self._session_factory() as session, session.begin():
            model = await session.scalar(
                select(QuerySafetyPolicyModel)
                .where(QuerySafetyPolicyModel.id == 1)
                .with_for_update()
            )
            current_revision = model.revision if model is not None else 0
            if current_revision != expected_revision:
                raise QuerySafetyPolicyRevisionConflictError
            values = {
                "estimated_rows_warning": policy.estimated_rows_warning,
                "estimated_rows_block": policy.estimated_rows_block,
                "total_cost_warning": policy.total_cost_warning,
                "total_cost_block": policy.total_cost_block,
                "join_count_warning": policy.join_count_warning,
                "join_count_block": policy.join_count_block,
                "sequential_scan_rows_warning": policy.sequential_scan_rows_warning,
                "sort_rows_warning": policy.sort_rows_warning,
                "stale_statistics_days": policy.stale_statistics_days,
            }
            if model is None:
                model = QuerySafetyPolicyModel(
                    id=1,
                    revision=1,
                    updated_by=actor_id,
                    updated_at=occurred_at,
                    **values,
                )
                session.add(model)
            else:
                model.revision += 1
                model.updated_by = actor_id
                model.updated_at = occurred_at
                for name, value in values.items():
                    setattr(model, name, value)
            session.add(
                AuditEventModel(
                    id=uuid4(),
                    occurred_at=occurred_at,
                    actor_type=AuditActorType.USER.value,
                    actor_id=actor_id,
                    action="query_safety.policy_replaced",
                    outcome=AuditOutcome.SUCCESS.value,
                    resource_type="query_safety_policy",
                    resource_id=None,
                    request_id=request_id,
                    details={"previous_revision": current_revision, "revision": model.revision},
                )
            )
            await session.flush()
            return _managed(model)
