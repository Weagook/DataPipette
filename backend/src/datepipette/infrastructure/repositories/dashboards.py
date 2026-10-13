"""Transactional persistence for private dashboards and chart references."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import and_, delete, exists, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from datepipette.application.dashboards import (
    DashboardAlreadyExistsError,
    DashboardCreate,
    DashboardRevisionConflictError,
)
from datepipette.domain.access_control import GrantPrincipalType
from datepipette.domain.audit import AuditActorType, AuditOutcome
from datepipette.domain.dashboards import Dashboard, DashboardDefinition, DashboardGrant
from datepipette.domain.identity import AccountStatus
from datepipette.infrastructure.models import (
    AuditEventModel,
    ChartModel,
    DashboardAclGrantModel,
    DashboardChartDependencyModel,
    DashboardModel,
    GroupMembershipModel,
    GroupModel,
    UserModel,
)


def _to_dashboard(model: DashboardModel) -> Dashboard:
    return Dashboard(
        id=model.id,
        owner_id=model.owner_id,
        name=model.name,
        revision=model.revision,
        definition=DashboardDefinition.from_document(model.definition),
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _to_grant(model: DashboardAclGrantModel) -> DashboardGrant:
    principal_id = model.user_id if model.principal_type == "user" else model.group_id
    if principal_id is None:  # pragma: no cover - database shape constraint
        raise RuntimeError("Dashboard grant has no principal")
    return DashboardGrant(
        model.id,
        model.dashboard_id,
        GrantPrincipalType(model.principal_type),
        principal_id,
        model.created_by,
        model.created_at,
    )


class SqlAlchemyDashboardStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(
        self,
        *,
        dashboard_id: UUID,
        actor_id: UUID,
        value: DashboardCreate,
        definition: dict[str, object],
        occurred_at: datetime,
        request_id: str,
    ) -> Dashboard:
        model = DashboardModel(
            id=dashboard_id,
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
                session.add_all(self._dependencies(dashboard_id, value.definition))
                session.add(
                    self._audit(
                        actor_id=actor_id,
                        action="dashboard.created",
                        resource_id=dashboard_id,
                        occurred_at=occurred_at,
                        request_id=request_id,
                        details={"revision": 1, "item_count": len(value.definition.items)},
                    )
                )
                await session.flush()
        except IntegrityError as exc:
            self._translate_integrity_error(exc)
            raise
        return _to_dashboard(model)

    async def list(
        self,
        *,
        actor_id: UUID,
        include_all: bool,
        after_created_at: datetime | None,
        after_id: UUID | None,
        limit: int,
    ) -> tuple[Dashboard, ...]:
        statement = (
            select(DashboardModel)
            .order_by(DashboardModel.created_at, DashboardModel.id)
            .limit(limit)
        )
        if not include_all:
            direct = exists().where(
                DashboardAclGrantModel.dashboard_id == DashboardModel.id,
                DashboardAclGrantModel.principal_type == GrantPrincipalType.USER.value,
                DashboardAclGrantModel.user_id == actor_id,
            )
            grouped = exists().where(
                DashboardAclGrantModel.dashboard_id == DashboardModel.id,
                DashboardAclGrantModel.principal_type == GrantPrincipalType.GROUP.value,
                GroupMembershipModel.group_id == DashboardAclGrantModel.group_id,
                GroupMembershipModel.user_id == actor_id,
            )
            statement = statement.where(or_(DashboardModel.owner_id == actor_id, direct, grouped))
        if after_created_at is not None and after_id is not None:
            statement = statement.where(
                or_(
                    DashboardModel.created_at > after_created_at,
                    and_(
                        DashboardModel.created_at == after_created_at,
                        DashboardModel.id > after_id,
                    ),
                )
            )
        async with self._session_factory() as session:
            models = tuple(await session.scalars(statement))
        return tuple(_to_dashboard(model) for model in models)

    async def get(self, dashboard_id: UUID) -> Dashboard | None:
        async with self._session_factory() as session:
            model = await session.get(DashboardModel, dashboard_id)
        return _to_dashboard(model) if model is not None else None

    async def has_view_access(self, *, dashboard_id: UUID, actor_id: UUID) -> bool:
        direct = exists().where(
            DashboardAclGrantModel.dashboard_id == dashboard_id,
            DashboardAclGrantModel.principal_type == GrantPrincipalType.USER.value,
            DashboardAclGrantModel.user_id == actor_id,
        )
        grouped = exists().where(
            DashboardAclGrantModel.dashboard_id == dashboard_id,
            DashboardAclGrantModel.principal_type == GrantPrincipalType.GROUP.value,
            GroupMembershipModel.group_id == DashboardAclGrantModel.group_id,
            GroupMembershipModel.user_id == actor_id,
        )
        async with self._session_factory() as session:
            return bool(await session.scalar(select(or_(direct, grouped))))

    async def list_grants(self, dashboard_id: UUID) -> tuple[DashboardGrant, ...] | None:
        async with self._session_factory() as session:
            if await session.get(DashboardModel, dashboard_id) is None:
                return None
            models = (
                await session.scalars(
                    select(DashboardAclGrantModel)
                    .where(DashboardAclGrantModel.dashboard_id == dashboard_id)
                    .order_by(DashboardAclGrantModel.created_at, DashboardAclGrantModel.id)
                )
            ).all()
        return tuple(_to_grant(model) for model in models)

    async def create_grant(
        self,
        *,
        grant_id: UUID,
        dashboard_id: UUID,
        principal_type: GrantPrincipalType,
        principal_id: UUID,
        actor_id: UUID,
        occurred_at: datetime,
        request_id: str,
    ) -> DashboardGrant | None:
        async with self._session_factory() as session, session.begin():
            if await session.get(DashboardModel, dashboard_id) is None:
                return None
            user_id: UUID | None = None
            group_id: UUID | None = None
            if principal_type is GrantPrincipalType.USER:
                user = await session.get(UserModel, principal_id)
                if user is None or user.status != AccountStatus.ACTIVE.value:
                    return None
                user_id = principal_id
            else:
                if await session.get(GroupModel, principal_id) is None:
                    return None
                group_id = principal_id
            existing = await session.scalar(
                select(DashboardAclGrantModel).where(
                    DashboardAclGrantModel.dashboard_id == dashboard_id,
                    DashboardAclGrantModel.principal_type == principal_type.value,
                    DashboardAclGrantModel.user_id == user_id,
                    DashboardAclGrantModel.group_id == group_id,
                )
            )
            if existing is not None:
                return _to_grant(existing)
            model = DashboardAclGrantModel(
                id=grant_id,
                dashboard_id=dashboard_id,
                principal_type=principal_type.value,
                user_id=user_id,
                group_id=group_id,
                created_by=actor_id,
                created_at=occurred_at,
            )
            session.add(model)
            session.add(
                self._audit(
                    actor_id=actor_id,
                    action="dashboard.grant_created",
                    resource_id=dashboard_id,
                    occurred_at=occurred_at,
                    request_id=request_id,
                    details={
                        "grant_id": str(grant_id),
                        "principal_type": principal_type.value,
                        "principal_id": str(principal_id),
                    },
                )
            )
            await session.flush()
            return _to_grant(model)

    async def revoke_grant(
        self,
        *,
        dashboard_id: UUID,
        grant_id: UUID,
        actor_id: UUID,
        occurred_at: datetime,
        request_id: str,
    ) -> bool | None:
        async with self._session_factory() as session, session.begin():
            if await session.get(DashboardModel, dashboard_id) is None:
                return None
            model = await session.scalar(
                select(DashboardAclGrantModel).where(
                    DashboardAclGrantModel.dashboard_id == dashboard_id,
                    DashboardAclGrantModel.id == grant_id,
                )
            )
            if model is None:
                return False
            await session.delete(model)
            session.add(
                self._audit(
                    actor_id=actor_id,
                    action="dashboard.grant_revoked",
                    resource_id=dashboard_id,
                    occurred_at=occurred_at,
                    request_id=request_id,
                    details={
                        "grant_id": str(grant_id),
                        "principal_type": model.principal_type,
                        "principal_id": str(model.user_id or model.group_id),
                    },
                )
            )
            return True

    async def resolve_chart_owners(self, chart_ids: frozenset[UUID]) -> dict[UUID, UUID]:
        if not chart_ids:
            return {}
        async with self._session_factory() as session:
            rows = tuple(
                await session.execute(
                    select(ChartModel.id, ChartModel.owner_id).where(ChartModel.id.in_(chart_ids))
                )
            )
        return dict(rows)

    async def update(
        self,
        *,
        dashboard_id: UUID,
        actor_id: UUID,
        expected_revision: int,
        name: str,
        definition: dict[str, object],
        occurred_at: datetime,
        request_id: str,
    ) -> Dashboard | None:
        try:
            async with self._session_factory() as session, session.begin():
                model = await session.scalar(
                    select(DashboardModel)
                    .where(DashboardModel.id == dashboard_id)
                    .with_for_update()
                )
                if model is None:
                    return None
                if model.revision != expected_revision:
                    raise DashboardRevisionConflictError
                if model.name != name or model.definition != definition:
                    parsed = DashboardDefinition.from_document(definition)
                    model.name = name
                    model.definition = definition
                    model.revision += 1
                    model.updated_at = occurred_at
                    await session.execute(
                        delete(DashboardChartDependencyModel).where(
                            DashboardChartDependencyModel.dashboard_id == dashboard_id
                        )
                    )
                    session.add_all(self._dependencies(dashboard_id, parsed))
                    session.add(
                        self._audit(
                            actor_id=actor_id,
                            action="dashboard.updated",
                            resource_id=dashboard_id,
                            occurred_at=occurred_at,
                            request_id=request_id,
                            details={
                                "revision": model.revision,
                                "item_count": len(parsed.items),
                            },
                        )
                    )
                    await session.flush()
                return _to_dashboard(model)
        except IntegrityError as exc:
            self._translate_integrity_error(exc)
            raise

    @staticmethod
    def _dependencies(
        dashboard_id: UUID,
        definition: DashboardDefinition,
    ) -> tuple[DashboardChartDependencyModel, ...]:
        return tuple(
            DashboardChartDependencyModel(
                id=uuid4(),
                dashboard_id=dashboard_id,
                chart_id=item.chart_id,
                item_id=item.id,
            )
            for item in definition.items
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
    ) -> AuditEventModel:
        return AuditEventModel(
            id=uuid4(),
            occurred_at=occurred_at,
            actor_type=AuditActorType.USER.value,
            actor_id=actor_id,
            action=action,
            outcome=AuditOutcome.SUCCESS.value,
            resource_type="dashboard",
            resource_id=resource_id,
            request_id=request_id,
            details=details,
        )

    @staticmethod
    def _translate_integrity_error(exc: IntegrityError) -> None:
        if "uq_dashboards_owner_name_normalized" in str(exc.orig):
            raise DashboardAlreadyExistsError from exc
