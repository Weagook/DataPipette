"""SQLAlchemy persistence and effective dataset access evaluation."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from datapipette.application.access_control import GroupAlreadyExistsError
from datapipette.domain.access_control import (
    DatasetAccess,
    DatasetGrant,
    DatasetPermission,
    GrantPrincipalType,
    Group,
    GroupMembership,
)
from datapipette.domain.audit import AuditActorType, AuditOutcome
from datapipette.domain.identity import AccountStatus, SystemRole
from datapipette.infrastructure.models import (
    AuditEventModel,
    DatasetAclGrantModel,
    DatasetModel,
    GroupMembershipModel,
    GroupModel,
    UserModel,
)


def _group(model: GroupModel) -> Group:
    return Group(model.id, model.name, model.created_by, model.created_at, model.updated_at)


def _membership(model: GroupMembershipModel) -> GroupMembership:
    return GroupMembership(model.group_id, model.user_id, model.created_by, model.created_at)


def _grant(model: DatasetAclGrantModel) -> DatasetGrant:
    principal_id = model.user_id if model.principal_type == "user" else model.group_id
    if principal_id is None:  # pragma: no cover - database shape constraint
        raise RuntimeError("ACL grant has no principal")
    return DatasetGrant(
        model.id,
        model.dataset_id,
        GrantPrincipalType(model.principal_type),
        principal_id,
        DatasetPermission(model.permission),
        model.created_by,
        model.created_at,
    )


class SqlAlchemyAccessControlStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_dataset_access(self, *, actor_id: UUID, dataset_id: UUID) -> DatasetAccess | None:
        async with self._session_factory() as session:
            owner_id = await session.scalar(
                select(DatasetModel.owner_id).where(DatasetModel.id == dataset_id)
            )
            if owner_id is None:
                return None
            if owner_id == actor_id:
                return DatasetAccess(dataset_id, True, frozenset(DatasetPermission))
            direct = await session.scalars(
                select(DatasetAclGrantModel.permission).where(
                    DatasetAclGrantModel.dataset_id == dataset_id,
                    DatasetAclGrantModel.principal_type == GrantPrincipalType.USER.value,
                    DatasetAclGrantModel.user_id == actor_id,
                )
            )
            grouped = await session.scalars(
                select(DatasetAclGrantModel.permission)
                .join(
                    GroupMembershipModel,
                    GroupMembershipModel.group_id == DatasetAclGrantModel.group_id,
                )
                .where(
                    DatasetAclGrantModel.dataset_id == dataset_id,
                    DatasetAclGrantModel.principal_type == GrantPrincipalType.GROUP.value,
                    GroupMembershipModel.user_id == actor_id,
                )
            )
            permissions = frozenset(
                DatasetPermission(value) for value in {*direct.all(), *grouped.all()}
            )
            return DatasetAccess(dataset_id, False, permissions)

    async def create_group(
        self,
        *,
        group_id: UUID,
        name: str,
        actor_id: UUID,
        occurred_at: datetime,
        request_id: str,
    ) -> Group:
        model = GroupModel(
            id=group_id,
            name=name,
            created_by=actor_id,
            created_at=occurred_at,
            updated_at=occurred_at,
        )
        try:
            async with self._session_factory() as session, session.begin():
                session.add(model)
                session.add(
                    self._audit(
                        actor_id=actor_id,
                        action="identity.group_created",
                        resource_type="group",
                        resource_id=group_id,
                        occurred_at=occurred_at,
                        request_id=request_id,
                        details={},
                    )
                )
                await session.flush()
        except IntegrityError as exc:
            if self._constraint(exc) == "uq_identity_groups_name_normalized":
                raise GroupAlreadyExistsError from exc
            raise
        return _group(model)

    async def list_groups(self, *, limit: int) -> tuple[Group, ...]:
        async with self._session_factory() as session:
            models = (
                await session.scalars(
                    select(GroupModel).order_by(GroupModel.name, GroupModel.id).limit(limit)
                )
            ).all()
        return tuple(_group(model) for model in models)

    async def get_group(self, group_id: UUID) -> Group | None:
        async with self._session_factory() as session:
            model = await session.get(GroupModel, group_id)
        return _group(model) if model is not None else None

    async def rename_group(
        self,
        *,
        group_id: UUID,
        name: str,
        actor_id: UUID,
        occurred_at: datetime,
        request_id: str,
    ) -> Group | None:
        try:
            async with self._session_factory() as session, session.begin():
                model = await session.get(GroupModel, group_id, with_for_update=True)
                if model is None:
                    return None
                if model.name != name:
                    model.name = name
                    model.updated_at = occurred_at
                    session.add(
                        self._audit(
                            actor_id=actor_id,
                            action="identity.group_renamed",
                            resource_type="group",
                            resource_id=group_id,
                            occurred_at=occurred_at,
                            request_id=request_id,
                            details={},
                        )
                    )
                    await session.flush()
                return _group(model)
        except IntegrityError as exc:
            if self._constraint(exc) == "uq_identity_groups_name_normalized":
                raise GroupAlreadyExistsError from exc
            raise

    async def list_members(self, group_id: UUID) -> tuple[GroupMembership, ...] | None:
        async with self._session_factory() as session:
            if await session.get(GroupModel, group_id) is None:
                return None
            models = (
                await session.scalars(
                    select(GroupMembershipModel)
                    .where(GroupMembershipModel.group_id == group_id)
                    .order_by(GroupMembershipModel.created_at, GroupMembershipModel.user_id)
                )
            ).all()
        return tuple(_membership(model) for model in models)

    async def add_member(
        self,
        *,
        group_id: UUID,
        user_id: UUID,
        actor_id: UUID,
        occurred_at: datetime,
        request_id: str,
    ) -> GroupMembership | None:
        async with self._session_factory() as session, session.begin():
            if await session.get(GroupModel, group_id) is None:
                return None
            user = await session.get(UserModel, user_id)
            if user is None or user.status != AccountStatus.ACTIVE.value:
                return None
            existing = await session.scalar(
                select(GroupMembershipModel).where(
                    GroupMembershipModel.group_id == group_id,
                    GroupMembershipModel.user_id == user_id,
                )
            )
            if existing is not None:
                return _membership(existing)
            model = GroupMembershipModel(
                id=uuid4(),
                group_id=group_id,
                user_id=user_id,
                created_by=actor_id,
                created_at=occurred_at,
            )
            session.add(model)
            session.add(
                self._audit(
                    actor_id=actor_id,
                    action="identity.group_member_added",
                    resource_type="group",
                    resource_id=group_id,
                    occurred_at=occurred_at,
                    request_id=request_id,
                    details={"user_id": str(user_id)},
                )
            )
            await session.flush()
            return _membership(model)

    async def remove_member(
        self,
        *,
        group_id: UUID,
        user_id: UUID,
        actor_id: UUID,
        occurred_at: datetime,
        request_id: str,
    ) -> bool | None:
        async with self._session_factory() as session, session.begin():
            if await session.get(GroupModel, group_id) is None:
                return None
            model = await session.scalar(
                select(GroupMembershipModel).where(
                    GroupMembershipModel.group_id == group_id,
                    GroupMembershipModel.user_id == user_id,
                )
            )
            if model is None:
                return False
            await session.delete(model)
            session.add(
                self._audit(
                    actor_id=actor_id,
                    action="identity.group_member_removed",
                    resource_type="group",
                    resource_id=group_id,
                    occurred_at=occurred_at,
                    request_id=request_id,
                    details={"user_id": str(user_id)},
                )
            )
            return True

    async def list_dataset_grants(self, dataset_id: UUID) -> tuple[DatasetGrant, ...] | None:
        async with self._session_factory() as session:
            if await session.get(DatasetModel, dataset_id) is None:
                return None
            models = (
                await session.scalars(
                    select(DatasetAclGrantModel)
                    .where(DatasetAclGrantModel.dataset_id == dataset_id)
                    .order_by(
                        DatasetAclGrantModel.created_at,
                        DatasetAclGrantModel.id,
                    )
                )
            ).all()
        return tuple(_grant(model) for model in models)

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
    ) -> DatasetGrant | None:
        async with self._session_factory() as session, session.begin():
            if await session.get(DatasetModel, dataset_id) is None:
                return None
            user_id: UUID | None = None
            group_id: UUID | None = None
            if principal_type is GrantPrincipalType.USER:
                user = await session.get(UserModel, principal_id)
                if (
                    user is None
                    or user.status != AccountStatus.ACTIVE.value
                    or user.role != SystemRole.ANALYST.value
                ):
                    return None
                user_id = principal_id
            else:
                if await session.get(GroupModel, principal_id) is None:
                    return None
                group_id = principal_id
            existing = await session.scalar(
                select(DatasetAclGrantModel).where(
                    DatasetAclGrantModel.dataset_id == dataset_id,
                    DatasetAclGrantModel.principal_type == principal_type.value,
                    DatasetAclGrantModel.user_id == user_id,
                    DatasetAclGrantModel.group_id == group_id,
                    DatasetAclGrantModel.permission == permission.value,
                )
            )
            if existing is not None:
                return _grant(existing)
            model = DatasetAclGrantModel(
                id=grant_id,
                dataset_id=dataset_id,
                principal_type=principal_type.value,
                user_id=user_id,
                group_id=group_id,
                permission=permission.value,
                created_by=actor_id,
                created_at=occurred_at,
            )
            session.add(model)
            session.add(
                self._audit(
                    actor_id=actor_id,
                    action="dataset.grant_created",
                    resource_type="dataset",
                    resource_id=dataset_id,
                    occurred_at=occurred_at,
                    request_id=request_id,
                    details={
                        "grant_id": str(grant_id),
                        "principal_type": principal_type.value,
                        "principal_id": str(principal_id),
                        "permission": permission.value,
                    },
                )
            )
            await session.flush()
            return _grant(model)

    async def revoke_dataset_grant(
        self,
        *,
        dataset_id: UUID,
        grant_id: UUID,
        actor_id: UUID,
        occurred_at: datetime,
        request_id: str,
    ) -> bool | None:
        async with self._session_factory() as session, session.begin():
            if await session.get(DatasetModel, dataset_id) is None:
                return None
            model = await session.scalar(
                select(DatasetAclGrantModel).where(
                    DatasetAclGrantModel.id == grant_id,
                    DatasetAclGrantModel.dataset_id == dataset_id,
                )
            )
            if model is None:
                return False
            await session.delete(model)
            session.add(
                self._audit(
                    actor_id=actor_id,
                    action="dataset.grant_revoked",
                    resource_type="dataset",
                    resource_id=dataset_id,
                    occurred_at=occurred_at,
                    request_id=request_id,
                    details={
                        "grant_id": str(grant_id),
                        "principal_type": model.principal_type,
                        "principal_id": str(model.user_id or model.group_id),
                        "permission": model.permission,
                    },
                )
            )
            return True

    @staticmethod
    def _constraint(exc: IntegrityError) -> str | None:
        return getattr(getattr(exc.orig, "diag", None), "constraint_name", None)

    @staticmethod
    def _audit(
        *,
        actor_id: UUID,
        action: str,
        resource_type: str,
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
            resource_type=resource_type,
            resource_id=resource_id,
            request_id=request_id,
            details=details,
        )
