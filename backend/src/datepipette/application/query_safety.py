"""Administrator-managed Query Safety policy."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from datepipette.domain.datasets import QuerySafetyPolicy
from datepipette.domain.identity import AccountStatus, SystemRole, User


class QuerySafetyPolicyRevisionConflictError(Exception):
    pass


class QuerySafetyAdministratorRequiredError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class ManagedQuerySafetyPolicy:
    policy: QuerySafetyPolicy
    revision: int
    updated_at: datetime | None
    updated_by: UUID | None


class QuerySafetyPolicyStore(Protocol):
    async def get(self) -> ManagedQuerySafetyPolicy: ...

    async def replace(
        self,
        *,
        policy: QuerySafetyPolicy,
        expected_revision: int,
        actor_id: UUID,
        occurred_at: datetime,
        request_id: str,
    ) -> ManagedQuerySafetyPolicy: ...


def require_query_safety_administrator(actor: User) -> None:
    if actor.role is not SystemRole.ADMIN or actor.status is not AccountStatus.ACTIVE:
        raise QuerySafetyAdministratorRequiredError


class QuerySafetyPolicyService:
    def __init__(self, store: QuerySafetyPolicyStore) -> None:
        self._store = store

    async def get(self, *, actor: User) -> ManagedQuerySafetyPolicy:
        require_query_safety_administrator(actor)
        return await self._store.get()

    async def replace(
        self,
        *,
        actor: User,
        policy: QuerySafetyPolicy,
        expected_revision: int,
        request_id: str,
    ) -> ManagedQuerySafetyPolicy:
        require_query_safety_administrator(actor)
        return await self._store.replace(
            policy=policy,
            expected_revision=expected_revision,
            actor_id=actor.id,
            occurred_at=datetime.now(UTC),
            request_id=request_id,
        )
