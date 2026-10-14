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


@dataclass(frozen=True, slots=True)
class InteractiveQueryRuntime:
    """One immutable set of DuckDB limits captured for a request."""

    timeout_seconds: float
    memory_limit_mb: int
    threads: int
    concurrency: int
    temporary_directory_limit_mb: int
    maximum_response_bytes: int

    @classmethod
    def from_policy(cls, policy: QuerySafetyPolicy) -> "InteractiveQueryRuntime":
        return cls(
            timeout_seconds=policy.interactive_query_timeout_seconds,
            memory_limit_mb=policy.interactive_query_memory_limit_mb,
            threads=policy.interactive_query_threads,
            concurrency=policy.interactive_query_concurrency,
            temporary_directory_limit_mb=policy.interactive_query_temp_limit_mb,
            maximum_response_bytes=policy.interactive_query_max_response_bytes,
        )


class QuerySafetyPolicyReader(Protocol):
    async def get(self) -> ManagedQuerySafetyPolicy: ...


class QuerySafetyPolicyStore(QuerySafetyPolicyReader, Protocol):
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
