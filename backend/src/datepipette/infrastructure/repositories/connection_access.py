"""SQLAlchemy persistence and evaluation for scoped connection access."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from datepipette.domain.access_control import GrantPrincipalType
from datepipette.domain.audit import AuditActorType, AuditOutcome
from datepipette.domain.connection_access import (
    ConnectionAccess,
    ConnectionGrant,
    ConnectionScope,
    ConnectionScopeType,
)
from datepipette.domain.identity import AccountStatus, SystemRole
from datepipette.infrastructure.models import (
    AuditEventModel,
    ConnectionAccessGrantModel,
    GroupMembershipModel,
    GroupModel,
    PostgresConnectionModel,
    UserModel,
)


def _grant(model: ConnectionAccessGrantModel) -> ConnectionGrant:
    principal_id = model.user_id if model.principal_type == "user" else model.group_id
    if principal_id is None:  # pragma: no cover - database shape constraint
        raise RuntimeError("Connection grant has no principal")
    return ConnectionGrant(
        id=model.id,
        connection_id=model.connection_id,
        principal_type=GrantPrincipalType(model.principal_type),
        principal_id=principal_id,
        scope=ConnectionScope(
            ConnectionScopeType(model.scope_type),
            model.schema_name,
            model.relation_name,
        ),
        created_by=model.created_by,
        created_at=model.created_at,
    )


def _access(connection_id: UUID, models: list[ConnectionAccessGrantModel]) -> ConnectionAccess:
    return ConnectionAccess(
        connection_id=connection_id,
        all_access=any(model.scope_type == ConnectionScopeType.ALL.value for model in models),
        schemas=frozenset(
            model.schema_name
            for model in models
            if model.scope_type == ConnectionScopeType.SCHEMA.value
            and model.schema_name is not None
        ),
        tables=frozenset(
            (model.schema_name, model.relation_name)
            for model in models
            if model.scope_type == ConnectionScopeType.TABLE.value
            and model.schema_name is not None
            and model.relation_name is not None
        ),
    )


class SqlAlchemyConnectionAccessStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_connection_access(
        self, *, actor_id: UUID, connection_id: UUID
    ) -> ConnectionAccess | None:
        async with self._session_factory() as session:
            if await session.get(PostgresConnectionModel, connection_id) is None:
                return None
            user = await session.get(UserModel, actor_id)
            if user is None or user.status != AccountStatus.ACTIVE.value:
                return None
            if user.role == SystemRole.ADMIN.value:
                return ConnectionAccess(connection_id, True, frozenset(), frozenset())
            if user.role != SystemRole.ANALYST.value:
                return None
            models = list(
                (
                    await session.scalars(
                        select(ConnectionAccessGrantModel).where(
                            ConnectionAccessGrantModel.connection_id == connection_id,
                            (
                                (
                                    ConnectionAccessGrantModel.principal_type
                                    == GrantPrincipalType.USER.value
                                )
                                & (ConnectionAccessGrantModel.user_id == actor_id)
                            )
                            | (
                                (
                                    ConnectionAccessGrantModel.principal_type
                                    == GrantPrincipalType.GROUP.value
                                )
                                & ConnectionAccessGrantModel.group_id.in_(
                                    select(GroupMembershipModel.group_id).where(
                                        GroupMembershipModel.user_id == actor_id
                                    )
                                )
                            ),
                        )
                    )
                ).all()
            )
        return _access(connection_id, models)

    async def list_accessible_connection_ids(self, actor_id: UUID) -> frozenset[UUID]:
        async with self._session_factory() as session:
            user = await session.get(UserModel, actor_id)
            if user is None or user.status != AccountStatus.ACTIVE.value:
                return frozenset()
            if user.role == SystemRole.ADMIN.value:
                return frozenset(await session.scalars(select(PostgresConnectionModel.id)))
            if user.role != SystemRole.ANALYST.value:
                return frozenset()
            identifiers = await session.scalars(
                select(ConnectionAccessGrantModel.connection_id)
                .where(
                    (
                        (ConnectionAccessGrantModel.principal_type == GrantPrincipalType.USER.value)
                        & (ConnectionAccessGrantModel.user_id == actor_id)
                    )
                    | (
                        (
                            ConnectionAccessGrantModel.principal_type
                            == GrantPrincipalType.GROUP.value
                        )
                        & ConnectionAccessGrantModel.group_id.in_(
                            select(GroupMembershipModel.group_id).where(
                                GroupMembershipModel.user_id == actor_id
                            )
                        )
                    )
                )
                .distinct()
            )
            return frozenset(identifiers)

    async def list_connection_grants(
        self, connection_id: UUID
    ) -> tuple[ConnectionGrant, ...] | None:
        async with self._session_factory() as session:
            if await session.get(PostgresConnectionModel, connection_id) is None:
                return None
            models = (
                await session.scalars(
                    select(ConnectionAccessGrantModel)
                    .where(ConnectionAccessGrantModel.connection_id == connection_id)
                    .order_by(
                        ConnectionAccessGrantModel.created_at,
                        ConnectionAccessGrantModel.id,
                    )
                )
            ).all()
        return tuple(_grant(model) for model in models)

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
    ) -> ConnectionGrant | None:
        async with self._session_factory() as session, session.begin():
            if await session.get(PostgresConnectionModel, connection_id) is None:
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
                select(ConnectionAccessGrantModel).where(
                    ConnectionAccessGrantModel.connection_id == connection_id,
                    ConnectionAccessGrantModel.principal_type == principal_type.value,
                    ConnectionAccessGrantModel.user_id == user_id,
                    ConnectionAccessGrantModel.group_id == group_id,
                    ConnectionAccessGrantModel.scope_type == scope.type.value,
                    ConnectionAccessGrantModel.schema_name == scope.schema_name,
                    ConnectionAccessGrantModel.relation_name == scope.relation_name,
                )
            )
            if existing is not None:
                return _grant(existing)
            model = ConnectionAccessGrantModel(
                id=grant_id,
                connection_id=connection_id,
                principal_type=principal_type.value,
                user_id=user_id,
                group_id=group_id,
                scope_type=scope.type.value,
                schema_name=scope.schema_name,
                relation_name=scope.relation_name,
                created_by=actor_id,
                created_at=occurred_at,
            )
            session.add(model)
            session.add(
                self._audit(
                    actor_id=actor_id,
                    action="connection.grant_created",
                    connection_id=connection_id,
                    occurred_at=occurred_at,
                    request_id=request_id,
                    details={
                        "grant_id": str(grant_id),
                        "principal_type": principal_type.value,
                        "principal_id": str(principal_id),
                        "scope_type": scope.type.value,
                        "schema_name": scope.schema_name,
                        "relation_name": scope.relation_name,
                    },
                )
            )
            await session.flush()
            return _grant(model)

    async def revoke_connection_grant(
        self,
        *,
        connection_id: UUID,
        grant_id: UUID,
        actor_id: UUID,
        occurred_at: datetime,
        request_id: str,
    ) -> bool | None:
        async with self._session_factory() as session, session.begin():
            if await session.get(PostgresConnectionModel, connection_id) is None:
                return None
            model = await session.scalar(
                select(ConnectionAccessGrantModel).where(
                    ConnectionAccessGrantModel.id == grant_id,
                    ConnectionAccessGrantModel.connection_id == connection_id,
                )
            )
            if model is None:
                return False
            principal_id = model.user_id or model.group_id
            await session.delete(model)
            session.add(
                self._audit(
                    actor_id=actor_id,
                    action="connection.grant_revoked",
                    connection_id=connection_id,
                    occurred_at=occurred_at,
                    request_id=request_id,
                    details={
                        "grant_id": str(grant_id),
                        "principal_type": model.principal_type,
                        "principal_id": str(principal_id),
                        "scope_type": model.scope_type,
                        "schema_name": model.schema_name,
                        "relation_name": model.relation_name,
                    },
                )
            )
            return True

    @staticmethod
    def _audit(
        *,
        actor_id: UUID,
        action: str,
        connection_id: UUID,
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
            resource_type="postgresql_connection",
            resource_id=connection_id,
            request_id=request_id,
            details=details,
        )
