"""Transactional persistence for administrator-managed users."""

from datetime import datetime
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy import update as sql_update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from datepipette.application.user_administration import (
    LastActiveAdministratorError,
    UserAlreadyExistsError,
    UserUpdate,
)
from datepipette.domain.audit import AuditActorType, AuditOutcome
from datepipette.domain.identity import AccountStatus, SystemRole, User
from datepipette.infrastructure.models import AuditEventModel, BrowserSessionModel, UserModel
from datepipette.infrastructure.repositories.authentication import to_user


class SqlAlchemyUserAdministrationStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create_user(
        self,
        *,
        actor_id: UUID,
        email: str,
        display_name: str,
        password_hash: str,
        role: SystemRole,
        occurred_at: datetime,
        request_id: str,
    ) -> User:
        user_model = UserModel(
            id=uuid4(),
            email=email,
            display_name=display_name,
            password_hash=password_hash,
            role=role.value,
            status=AccountStatus.ACTIVE.value,
            last_login_at=None,
            created_at=occurred_at,
            updated_at=occurred_at,
        )
        try:
            async with self._session_factory() as session, session.begin():
                session.add(user_model)
                session.add(
                    self._audit_event(
                        actor_id=actor_id,
                        action="identity.user_created",
                        resource_id=user_model.id,
                        occurred_at=occurred_at,
                        request_id=request_id,
                        details={"role": role.value},
                    )
                )
                await session.flush()
        except IntegrityError as exc:
            if getattr(getattr(exc.orig, "diag", None), "constraint_name", None) == (
                "uq_users_email"
            ):
                raise UserAlreadyExistsError from exc
            raise
        return to_user(user_model)

    async def list_users(
        self,
        *,
        after_created_at: datetime | None,
        after_id: UUID | None,
        limit: int,
    ) -> list[User]:
        statement = select(UserModel).order_by(UserModel.created_at, UserModel.id).limit(limit)
        if after_created_at is not None and after_id is not None:
            statement = statement.where(
                or_(
                    UserModel.created_at > after_created_at,
                    and_(
                        UserModel.created_at == after_created_at,
                        UserModel.id > after_id,
                    ),
                )
            )
        async with self._session_factory() as session:
            models = (await session.scalars(statement)).all()
        return [to_user(model) for model in models]

    async def get_user(self, user_id: UUID) -> User | None:
        async with self._session_factory() as session:
            model = await session.get(UserModel, user_id)
        return to_user(model) if model is not None else None

    async def update_user(
        self,
        *,
        actor_id: UUID,
        user_id: UUID,
        update: UserUpdate,
        occurred_at: datetime,
        request_id: str,
    ) -> User | None:
        async with self._session_factory() as session, session.begin():
            await session.execute(text("LOCK TABLE users IN SHARE ROW EXCLUSIVE MODE"))
            model = await session.get(UserModel, user_id)
            if model is None:
                return None

            target_role = update.role.value if update.role is not None else model.role
            target_status = update.status.value if update.status is not None else model.status
            removes_active_admin = (
                model.role == SystemRole.ADMIN.value
                and model.status == AccountStatus.ACTIVE.value
                and (
                    target_role != SystemRole.ADMIN.value
                    or target_status != AccountStatus.ACTIVE.value
                )
            )
            if removes_active_admin:
                active_admins = await session.scalar(
                    select(func.count())
                    .select_from(UserModel)
                    .where(
                        UserModel.role == SystemRole.ADMIN.value,
                        UserModel.status == AccountStatus.ACTIVE.value,
                    )
                )
                if active_admins is None or active_admins <= 1:
                    raise LastActiveAdministratorError

            changes: dict[str, Any] = {}
            if update.display_name is not None and update.display_name != model.display_name:
                changes["display_name"] = {
                    "from": model.display_name,
                    "to": update.display_name,
                }
                model.display_name = update.display_name
            if target_role != model.role:
                changes["role"] = {"from": model.role, "to": target_role}
                model.role = target_role
            if target_status != model.status:
                changes["status"] = {"from": model.status, "to": target_status}
                model.status = target_status
                if target_status == AccountStatus.DISABLED.value:
                    await session.execute(
                        sql_update(BrowserSessionModel)
                        .where(
                            BrowserSessionModel.user_id == user_id,
                            BrowserSessionModel.revoked_at.is_(None),
                        )
                        .values(revoked_at=occurred_at)
                    )

            if changes:
                model.updated_at = occurred_at
                session.add(
                    self._audit_event(
                        actor_id=actor_id,
                        action="identity.user_updated",
                        resource_id=user_id,
                        occurred_at=occurred_at,
                        request_id=request_id,
                        details={"changes": changes},
                    )
                )
                await session.flush()
            return to_user(model)

    async def revoke_user_sessions(
        self,
        *,
        actor_id: UUID,
        user_id: UUID,
        occurred_at: datetime,
        request_id: str,
    ) -> int | None:
        async with self._session_factory() as session, session.begin():
            if await session.get(UserModel, user_id) is None:
                return None
            result = cast(
                CursorResult[Any],
                await session.execute(
                    sql_update(BrowserSessionModel)
                    .where(
                        BrowserSessionModel.user_id == user_id,
                        BrowserSessionModel.revoked_at.is_(None),
                        BrowserSessionModel.expires_at > occurred_at,
                    )
                    .values(revoked_at=occurred_at)
                ),
            )
            revoked_count = result.rowcount
            session.add(
                self._audit_event(
                    actor_id=actor_id,
                    action="identity.user_sessions_revoked",
                    resource_id=user_id,
                    occurred_at=occurred_at,
                    request_id=request_id,
                    details={"revoked_count": revoked_count},
                )
            )
            return revoked_count

    @staticmethod
    def _audit_event(
        *,
        actor_id: UUID,
        action: str,
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
            resource_type="user",
            resource_id=resource_id,
            request_id=request_id,
            details=details,
        )
