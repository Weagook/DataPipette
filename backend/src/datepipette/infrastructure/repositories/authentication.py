"""Transactional SQLAlchemy authentication store."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from datepipette.application.authentication import SessionSecrets
from datepipette.domain.audit import AuditActorType, AuditOutcome
from datepipette.domain.identity import (
    AccountStatus,
    AuthenticatedSession,
    SystemRole,
    User,
    UserCredentials,
)
from datepipette.infrastructure.models import AuditEventModel, BrowserSessionModel, UserModel


def to_user(model: UserModel) -> User:
    return User(
        id=model.id,
        email=model.email,
        display_name=model.display_name,
        role=SystemRole(model.role),
        status=AccountStatus(model.status),
        last_login_at=model.last_login_at,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


class SqlAlchemyAuthenticationStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_credentials(self, normalized_email: str) -> UserCredentials | None:
        async with self._session_factory() as session:
            model = await session.scalar(
                select(UserModel).where(UserModel.email == normalized_email)
            )
        if model is None:
            return None
        return UserCredentials(user=to_user(model), password_hash=model.password_hash)

    async def create_first_admin(
        self,
        *,
        email: str,
        display_name: str,
        password_hash: str,
        occurred_at: datetime,
    ) -> User | None:
        async with self._session_factory() as session, session.begin():
            await session.execute(text("LOCK TABLE users IN SHARE ROW EXCLUSIVE MODE"))
            admin_count = await session.scalar(
                select(func.count()).select_from(UserModel).where(UserModel.role == "admin")
            )
            if admin_count:
                return None

            user_model = UserModel(
                id=uuid4(),
                email=email,
                display_name=display_name,
                password_hash=password_hash,
                role=SystemRole.ADMIN.value,
                status=AccountStatus.ACTIVE.value,
                last_login_at=None,
                created_at=occurred_at,
                updated_at=occurred_at,
            )
            session.add(user_model)
            session.add(
                AuditEventModel(
                    id=uuid4(),
                    occurred_at=occurred_at,
                    actor_type=AuditActorType.SYSTEM.value,
                    actor_id=None,
                    action="identity.admin_bootstrapped",
                    outcome=AuditOutcome.SUCCESS.value,
                    resource_type="user",
                    resource_id=user_model.id,
                    request_id=None,
                    details={},
                )
            )
            await session.flush()
            return to_user(user_model)

    async def create_login_session(
        self,
        *,
        user_id: UUID,
        secrets: SessionSecrets,
        created_at: datetime,
        expires_at: datetime,
        request_id: str,
    ) -> UUID:
        session_id = uuid4()
        async with self._session_factory() as session, session.begin():
            session.add(
                BrowserSessionModel(
                    id=session_id,
                    user_id=user_id,
                    token_hash=secrets.session_token_hash,
                    csrf_token_hash=secrets.csrf_token_hash,
                    expires_at=expires_at,
                    revoked_at=None,
                    last_seen_at=created_at,
                    created_at=created_at,
                )
            )
            await session.execute(
                update(UserModel)
                .where(UserModel.id == user_id)
                .values(last_login_at=created_at, updated_at=created_at)
            )
            session.add(
                AuditEventModel(
                    id=uuid4(),
                    occurred_at=created_at,
                    actor_type=AuditActorType.USER.value,
                    actor_id=user_id,
                    action="auth.login",
                    outcome=AuditOutcome.SUCCESS.value,
                    resource_type="browser_session",
                    resource_id=session_id,
                    request_id=request_id,
                    details={},
                )
            )
        return session_id

    async def record_login_failure(
        self,
        *,
        occurred_at: datetime,
        request_id: str,
        outcome: str,
        reason: str,
    ) -> None:
        async with self._session_factory() as session, session.begin():
            session.add(
                AuditEventModel(
                    id=uuid4(),
                    occurred_at=occurred_at,
                    actor_type=AuditActorType.ANONYMOUS.value,
                    actor_id=None,
                    action="auth.login",
                    outcome=outcome,
                    resource_type=None,
                    resource_id=None,
                    request_id=request_id,
                    details={"reason": reason},
                )
            )

    async def get_session(self, token_hash: str) -> AuthenticatedSession | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(BrowserSessionModel, UserModel)
                    .join(UserModel, UserModel.id == BrowserSessionModel.user_id)
                    .where(BrowserSessionModel.token_hash == token_hash)
                )
            ).one_or_none()
        if row is None:
            return None
        session_model, user_model = row
        return AuthenticatedSession(
            id=session_model.id,
            user=to_user(user_model),
            csrf_token_hash=session_model.csrf_token_hash,
            expires_at=session_model.expires_at,
            revoked_at=session_model.revoked_at,
        )

    async def revoke_session(
        self,
        *,
        session_id: UUID,
        user_id: UUID,
        occurred_at: datetime,
        request_id: str,
    ) -> None:
        async with self._session_factory() as session, session.begin():
            await session.execute(
                update(BrowserSessionModel)
                .where(
                    BrowserSessionModel.id == session_id,
                    BrowserSessionModel.revoked_at.is_(None),
                )
                .values(revoked_at=occurred_at)
            )
            session.add(
                AuditEventModel(
                    id=uuid4(),
                    occurred_at=occurred_at,
                    actor_type=AuditActorType.USER.value,
                    actor_id=user_id,
                    action="auth.logout",
                    outcome=AuditOutcome.SUCCESS.value,
                    resource_type="browser_session",
                    resource_id=session_id,
                    request_id=request_id,
                    details={},
                )
            )
