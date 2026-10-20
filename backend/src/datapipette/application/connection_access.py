"""Connection grants and authoritative effective-scope authorization."""

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from datapipette.domain.access_control import GrantPrincipalType
from datapipette.domain.connection_access import (
    ConnectionAccess,
    ConnectionGrant,
    ConnectionScope,
    ConnectionScopeType,
)
from datapipette.domain.identity import AccountStatus, SystemRole, User


class ConnectionAccessDeniedError(Exception):
    pass


class ConnectionGrantNotFoundError(Exception):
    pass


class ConnectionGrantPrincipalNotFoundError(Exception):
    pass


class ConnectionGrantAdministratorRequiredError(Exception):
    pass


class ConnectionAccessStore(Protocol):
    async def get_connection_access(
        self, *, actor_id: UUID, connection_id: UUID
    ) -> ConnectionAccess | None: ...

    async def list_accessible_connection_ids(self, actor_id: UUID) -> frozenset[UUID]: ...


class ConnectionGrantStore(ConnectionAccessStore, Protocol):
    async def list_connection_grants(
        self, connection_id: UUID
    ) -> tuple[ConnectionGrant, ...] | None: ...

    async def create_connection_grant(
        self,
        *,
        grant_id: UUID,
        connection_id: UUID,
        principal_type: GrantPrincipalType,
        principal_id: UUID,
        scope: ConnectionScope,
        actor_id: UUID,
        occurred_at: datetime,
        request_id: str,
    ) -> ConnectionGrant | None: ...

    async def revoke_connection_grant(
        self,
        *,
        connection_id: UUID,
        grant_id: UUID,
        actor_id: UUID,
        occurred_at: datetime,
        request_id: str,
    ) -> bool | None: ...


def _identifier(value: str | None, label: str) -> str:
    if value is None or not value.strip() or "\x00" in value:
        raise ValueError(f"{label} must not be empty")
    if len(value) > 255:
        raise ValueError(f"{label} must contain at most 255 characters")
    return value


def normalize_scope(scope: ConnectionScope) -> ConnectionScope:
    if scope.type is ConnectionScopeType.ALL:
        if scope.schema_name is not None or scope.relation_name is not None:
            raise ValueError("All scope cannot contain schema or relation names")
        return scope
    schema_name = _identifier(scope.schema_name, "Schema name")
    if scope.type is ConnectionScopeType.SCHEMA:
        if scope.relation_name is not None:
            raise ValueError("Schema scope cannot contain a relation name")
        return ConnectionScope(scope.type, schema_name, None)
    return ConnectionScope(
        scope.type,
        schema_name,
        _identifier(scope.relation_name, "Relation name"),
    )


class ConnectionAccessAuthorizer:
    def __init__(self, store: ConnectionAccessStore) -> None:
        self._store = store

    async def require_connection(self, *, actor: User, connection_id: UUID) -> ConnectionAccess:
        if actor.status is not AccountStatus.ACTIVE or actor.role is SystemRole.VIEWER:
            raise ConnectionAccessDeniedError
        access = await self._store.get_connection_access(
            actor_id=actor.id,
            connection_id=connection_id,
        )
        if access is None or access.is_empty:
            raise ConnectionAccessDeniedError
        return access

    async def require_relation(
        self,
        *,
        actor: User,
        connection_id: UUID,
        schema_name: str,
        relation_name: str,
    ) -> ConnectionAccess:
        access = await self.require_connection(actor=actor, connection_id=connection_id)
        if not access.allows_relation(schema_name, relation_name):
            raise ConnectionAccessDeniedError
        return access

    async def require_relations(
        self,
        *,
        actor: User,
        connection_id: UUID,
        relations: Iterable[tuple[str, str]],
    ) -> ConnectionAccess:
        access = await self.require_connection(actor=actor, connection_id=connection_id)
        if any(
            not access.allows_relation(schema_name, relation_name)
            for schema_name, relation_name in relations
        ):
            raise ConnectionAccessDeniedError
        return access


class ConnectionGrantService:
    def __init__(self, store: ConnectionGrantStore) -> None:
        self._store = store

    @staticmethod
    def _require_admin(actor: User) -> None:
        if actor.status is not AccountStatus.ACTIVE or actor.role is not SystemRole.ADMIN:
            raise ConnectionGrantAdministratorRequiredError

    async def list(self, *, actor: User, connection_id: UUID) -> tuple[ConnectionGrant, ...]:
        self._require_admin(actor)
        grants = await self._store.list_connection_grants(connection_id)
        if grants is None:
            raise ConnectionAccessDeniedError
        return grants

    async def create(
        self,
        *,
        actor: User,
        connection_id: UUID,
        principal_type: GrantPrincipalType,
        principal_id: UUID,
        scope: ConnectionScope,
        request_id: str,
    ) -> ConnectionGrant:
        self._require_admin(actor)
        grant = await self._store.create_connection_grant(
            grant_id=uuid4(),
            connection_id=connection_id,
            principal_type=principal_type,
            principal_id=principal_id,
            scope=normalize_scope(scope),
            actor_id=actor.id,
            occurred_at=datetime.now(UTC),
            request_id=request_id,
        )
        if grant is None:
            raise ConnectionGrantPrincipalNotFoundError
        return grant

    async def revoke(
        self,
        *,
        actor: User,
        connection_id: UUID,
        grant_id: UUID,
        request_id: str,
    ) -> None:
        self._require_admin(actor)
        result = await self._store.revoke_connection_grant(
            connection_id=connection_id,
            grant_id=grant_id,
            actor_id=actor.id,
            occurred_at=datetime.now(UTC),
            request_id=request_id,
        )
        if result is None:
            raise ConnectionAccessDeniedError
        if not result:
            raise ConnectionGrantNotFoundError
