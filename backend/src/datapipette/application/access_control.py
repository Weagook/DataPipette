"""Application services for groups and additive dataset ACLs."""

from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from datapipette.domain.access_control import (
    DatasetAccess,
    DatasetGrant,
    DatasetPermission,
    GrantPrincipalType,
    Group,
    GroupMembership,
)
from datapipette.domain.identity import AccountStatus, SystemRole, User


class AccessAdministratorRequiredError(Exception):
    pass


class DatasetAccessDeniedError(Exception):
    pass


class GroupNotFoundError(Exception):
    pass


class GroupAlreadyExistsError(Exception):
    pass


class GroupMemberNotFoundError(Exception):
    pass


class GrantPrincipalNotFoundError(Exception):
    pass


class DatasetGrantNotFoundError(Exception):
    pass


class DatasetAccessStore(Protocol):
    async def get_dataset_access(
        self, *, actor_id: UUID, dataset_id: UUID
    ) -> DatasetAccess | None: ...


class AccessControlStore(DatasetAccessStore, Protocol):
    async def create_group(
        self,
        *,
        group_id: UUID,
        name: str,
        actor_id: UUID,
        occurred_at: datetime,
        request_id: str,
    ) -> Group: ...

    async def list_groups(self, *, limit: int) -> tuple[Group, ...]: ...
    async def get_group(self, group_id: UUID) -> Group | None: ...
    async def rename_group(
        self,
        *,
        group_id: UUID,
        name: str,
        actor_id: UUID,
        occurred_at: datetime,
        request_id: str,
    ) -> Group | None: ...
    async def list_members(self, group_id: UUID) -> tuple[GroupMembership, ...] | None: ...
    async def add_member(
        self,
        *,
        group_id: UUID,
        user_id: UUID,
        actor_id: UUID,
        occurred_at: datetime,
        request_id: str,
    ) -> GroupMembership | None: ...
    async def remove_member(
        self,
        *,
        group_id: UUID,
        user_id: UUID,
        actor_id: UUID,
        occurred_at: datetime,
        request_id: str,
    ) -> bool | None: ...
    async def list_dataset_grants(self, dataset_id: UUID) -> tuple[DatasetGrant, ...] | None: ...
    async def create_dataset_grant(
        self,
        *,
        grant_id: UUID,
        dataset_id: UUID,
        principal_type: GrantPrincipalType,
        principal_id: UUID,
        permission: DatasetPermission,
        actor_id: UUID,
        occurred_at: datetime,
        request_id: str,
    ) -> DatasetGrant | None: ...
    async def revoke_dataset_grant(
        self,
        *,
        dataset_id: UUID,
        grant_id: UUID,
        actor_id: UUID,
        occurred_at: datetime,
        request_id: str,
    ) -> bool | None: ...


class DatasetAccessAuthorizer:
    def __init__(self, store: DatasetAccessStore) -> None:
        self._store = store

    async def require(
        self,
        *,
        actor: User,
        dataset_id: UUID,
        permissions: frozenset[DatasetPermission],
    ) -> DatasetAccess:
        if actor.status is not AccountStatus.ACTIVE:
            raise DatasetAccessDeniedError
        if actor.role is SystemRole.ADMIN:
            return DatasetAccess(dataset_id, False, frozenset(DatasetPermission))
        if actor.role is SystemRole.VIEWER:
            raise DatasetAccessDeniedError
        access = await self._store.get_dataset_access(actor_id=actor.id, dataset_id=dataset_id)
        if access is None or not permissions.issubset(access.permissions):
            raise DatasetAccessDeniedError
        return access


def _require_admin(actor: User) -> None:
    if actor.role is not SystemRole.ADMIN or actor.status is not AccountStatus.ACTIVE:
        raise AccessAdministratorRequiredError


def _group_name(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError("Group name must not be empty")
    if len(normalized) > 100:
        raise ValueError("Group name must contain at most 100 characters")
    return normalized


class GroupService:
    def __init__(self, store: AccessControlStore) -> None:
        self._store = store

    async def create(self, *, actor: User, name: str, request_id: str) -> Group:
        _require_admin(actor)
        return await self._store.create_group(
            group_id=uuid4(),
            name=_group_name(name),
            actor_id=actor.id,
            occurred_at=datetime.now(UTC),
            request_id=request_id,
        )

    async def list(self, *, actor: User, limit: int) -> tuple[Group, ...]:
        _require_admin(actor)
        return await self._store.list_groups(limit=limit)

    async def get(self, *, actor: User, group_id: UUID) -> Group:
        _require_admin(actor)
        group = await self._store.get_group(group_id)
        if group is None:
            raise GroupNotFoundError
        return group

    async def rename(self, *, actor: User, group_id: UUID, name: str, request_id: str) -> Group:
        _require_admin(actor)
        group = await self._store.rename_group(
            group_id=group_id,
            name=_group_name(name),
            actor_id=actor.id,
            occurred_at=datetime.now(UTC),
            request_id=request_id,
        )
        if group is None:
            raise GroupNotFoundError
        return group

    async def list_members(self, *, actor: User, group_id: UUID) -> tuple[GroupMembership, ...]:
        _require_admin(actor)
        memberships = await self._store.list_members(group_id)
        if memberships is None:
            raise GroupNotFoundError
        return memberships

    async def add_member(
        self, *, actor: User, group_id: UUID, user_id: UUID, request_id: str
    ) -> GroupMembership:
        _require_admin(actor)
        membership = await self._store.add_member(
            group_id=group_id,
            user_id=user_id,
            actor_id=actor.id,
            occurred_at=datetime.now(UTC),
            request_id=request_id,
        )
        if membership is None:
            raise GroupMemberNotFoundError
        return membership

    async def remove_member(
        self, *, actor: User, group_id: UUID, user_id: UUID, request_id: str
    ) -> None:
        _require_admin(actor)
        result = await self._store.remove_member(
            group_id=group_id,
            user_id=user_id,
            actor_id=actor.id,
            occurred_at=datetime.now(UTC),
            request_id=request_id,
        )
        if result is None:
            raise GroupNotFoundError
        if not result:
            raise GroupMemberNotFoundError


class DatasetGrantService:
    def __init__(
        self,
        store: AccessControlStore,
        authorizer: DatasetAccessAuthorizer,
    ) -> None:
        self._store = store
        self._authorizer = authorizer

    async def list(self, *, actor: User, dataset_id: UUID) -> tuple[DatasetGrant, ...]:
        await self._authorizer.require(
            actor=actor,
            dataset_id=dataset_id,
            permissions=frozenset({DatasetPermission.MANAGE}),
        )
        grants = await self._store.list_dataset_grants(dataset_id)
        if grants is None:
            raise DatasetAccessDeniedError
        return grants

    async def create(
        self,
        *,
        actor: User,
        dataset_id: UUID,
        principal_type: GrantPrincipalType,
        principal_id: UUID,
        permission: DatasetPermission,
        request_id: str,
    ) -> DatasetGrant:
        await self._authorizer.require(
            actor=actor,
            dataset_id=dataset_id,
            permissions=frozenset({DatasetPermission.MANAGE}),
        )
        grant = await self._store.create_dataset_grant(
            grant_id=uuid4(),
            dataset_id=dataset_id,
            principal_type=principal_type,
            principal_id=principal_id,
            permission=permission,
            actor_id=actor.id,
            occurred_at=datetime.now(UTC),
            request_id=request_id,
        )
        if grant is None:
            raise GrantPrincipalNotFoundError
        return grant

    async def revoke(
        self,
        *,
        actor: User,
        dataset_id: UUID,
        grant_id: UUID,
        request_id: str,
    ) -> None:
        await self._authorizer.require(
            actor=actor,
            dataset_id=dataset_id,
            permissions=frozenset({DatasetPermission.MANAGE}),
        )
        result = await self._store.revoke_dataset_grant(
            dataset_id=dataset_id,
            grant_id=grant_id,
            actor_id=actor.id,
            occurred_at=datetime.now(UTC),
            request_id=request_id,
        )
        if result is None:
            raise DatasetAccessDeniedError
        if not result:
            raise DatasetGrantNotFoundError
