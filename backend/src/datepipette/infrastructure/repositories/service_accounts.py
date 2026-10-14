"""SQLAlchemy persistence for service accounts and scoped API tokens."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from datepipette.application.service_accounts import (
    ServiceAccountAlreadyExistsError,
    ServiceAccountUpdate,
)
from datepipette.domain.audit import AuditActorType, AuditOutcome
from datepipette.domain.identity import AccountStatus, AccountType, SystemRole
from datepipette.domain.service_accounts import (
    ApiToken,
    ApiTokenScope,
    AuthenticatedApiToken,
    ServiceAccount,
)
from datepipette.infrastructure.models import ApiTokenModel, AuditEventModel, UserModel
from datepipette.infrastructure.repositories.authentication import to_user


def _account(model: UserModel) -> ServiceAccount:
    return ServiceAccount(
        model.id,
        model.display_name,
        SystemRole(model.role),
        AccountStatus(model.status),
        model.created_at,
        model.updated_at,
    )


def _token(model: ApiTokenModel) -> ApiToken:
    return ApiToken(
        model.id,
        model.service_account_id,
        model.name,
        frozenset(ApiTokenScope(value) for value in model.scopes),
        model.expires_at,
        model.revoked_at,
        model.last_used_at,
        model.created_by,
        model.created_at,
    )


class SqlAlchemyServiceAccountStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create_account(
        self,
        *,
        account_id: UUID,
        name: str,
        role: SystemRole,
        actor_id: UUID,
        occurred_at: datetime,
        request_id: str,
    ) -> ServiceAccount:
        model = UserModel(
            id=account_id,
            email=f"service-{account_id}@service.datepipette.invalid",
            display_name=name,
            password_hash="!service-account",
            role=role.value,
            status=AccountStatus.ACTIVE.value,
            last_login_at=None,
            created_at=occurred_at,
            updated_at=occurred_at,
            account_type=AccountType.SERVICE.value,
        )
        try:
            async with self._session_factory() as session, session.begin():
                session.add(model)
                session.add(
                    self._audit(
                        actor_id=actor_id,
                        action="identity.service_account_created",
                        resource_type="service_account",
                        resource_id=account_id,
                        occurred_at=occurred_at,
                        request_id=request_id,
                        details={"role": role.value},
                    )
                )
                await session.flush()
        except IntegrityError as exc:
            if "uq_service_accounts_name_normalized" in str(exc.orig):
                raise ServiceAccountAlreadyExistsError from exc
            raise
        return _account(model)

    async def list_accounts(self, *, limit: int) -> tuple[ServiceAccount, ...]:
        async with self._session_factory() as session:
            models = (
                await session.scalars(
                    select(UserModel)
                    .where(UserModel.account_type == AccountType.SERVICE.value)
                    .order_by(UserModel.created_at, UserModel.id)
                    .limit(limit)
                )
            ).all()
        return tuple(_account(model) for model in models)

    async def get_account(self, account_id: UUID) -> ServiceAccount | None:
        async with self._session_factory() as session:
            model = await self._get_account_model(session, account_id)
        return _account(model) if model is not None else None

    async def update_account(
        self,
        *,
        account_id: UUID,
        update: ServiceAccountUpdate,
        actor_id: UUID,
        occurred_at: datetime,
        request_id: str,
    ) -> ServiceAccount | None:
        try:
            async with self._session_factory() as session, session.begin():
                model = await self._get_account_model(session, account_id, for_update=True)
                if model is None:
                    return None
                changes: dict[str, object] = {}
                if update.name is not None and update.name != model.display_name:
                    changes["name"] = {"from": model.display_name, "to": update.name}
                    model.display_name = update.name
                if update.role is not None and update.role.value != model.role:
                    changes["role"] = {"from": model.role, "to": update.role.value}
                    model.role = update.role.value
                if update.status is not None and update.status.value != model.status:
                    changes["status"] = {"from": model.status, "to": update.status.value}
                    model.status = update.status.value
                if changes:
                    model.updated_at = occurred_at
                    session.add(
                        self._audit(
                            actor_id=actor_id,
                            action="identity.service_account_updated",
                            resource_type="service_account",
                            resource_id=account_id,
                            occurred_at=occurred_at,
                            request_id=request_id,
                            details={"changes": changes},
                        )
                    )
                    await session.flush()
                return _account(model)
        except IntegrityError as exc:
            if "uq_service_accounts_name_normalized" in str(exc.orig):
                raise ServiceAccountAlreadyExistsError from exc
            raise

    async def create_token(
        self,
        *,
        token_id: UUID,
        account_id: UUID,
        name: str,
        token_hash: str,
        scopes: frozenset[ApiTokenScope],
        expires_at: datetime | None,
        actor_id: UUID,
        occurred_at: datetime,
        request_id: str,
    ) -> ApiToken | None:
        async with self._session_factory() as session, session.begin():
            if await self._get_account_model(session, account_id) is None:
                return None
            model = ApiTokenModel(
                id=token_id,
                service_account_id=account_id,
                name=name,
                token_hash=token_hash,
                scopes=sorted(scope.value for scope in scopes),
                expires_at=expires_at,
                revoked_at=None,
                last_used_at=None,
                created_by=actor_id,
                created_at=occurred_at,
            )
            session.add(model)
            session.add(
                self._audit(
                    actor_id=actor_id,
                    action="identity.api_token_created",
                    resource_type="api_token",
                    resource_id=token_id,
                    occurred_at=occurred_at,
                    request_id=request_id,
                    details={
                        "service_account_id": str(account_id),
                        "scopes": sorted(scope.value for scope in scopes),
                        "expires_at": expires_at.isoformat() if expires_at is not None else None,
                    },
                )
            )
            await session.flush()
            return _token(model)

    async def list_tokens(self, account_id: UUID) -> tuple[ApiToken, ...] | None:
        async with self._session_factory() as session:
            if await self._get_account_model(session, account_id) is None:
                return None
            models = (
                await session.scalars(
                    select(ApiTokenModel)
                    .where(ApiTokenModel.service_account_id == account_id)
                    .order_by(ApiTokenModel.created_at, ApiTokenModel.id)
                )
            ).all()
        return tuple(_token(model) for model in models)

    async def revoke_token(
        self,
        *,
        account_id: UUID,
        token_id: UUID,
        actor_id: UUID,
        occurred_at: datetime,
        request_id: str,
    ) -> bool | None:
        async with self._session_factory() as session, session.begin():
            if await self._get_account_model(session, account_id) is None:
                return None
            model = await session.scalar(
                select(ApiTokenModel).where(
                    ApiTokenModel.id == token_id,
                    ApiTokenModel.service_account_id == account_id,
                )
            )
            if model is None:
                return False
            if model.revoked_at is None:
                model.revoked_at = occurred_at
                session.add(
                    self._audit(
                        actor_id=actor_id,
                        action="identity.api_token_revoked",
                        resource_type="api_token",
                        resource_id=token_id,
                        occurred_at=occurred_at,
                        request_id=request_id,
                        details={"service_account_id": str(account_id)},
                    )
                )
            return True

    async def authenticate_token(
        self, *, token_hash: str, used_at: datetime
    ) -> AuthenticatedApiToken | None:
        async with self._session_factory() as session, session.begin():
            row = (
                await session.execute(
                    select(ApiTokenModel, UserModel)
                    .join(UserModel, UserModel.id == ApiTokenModel.service_account_id)
                    .where(
                        ApiTokenModel.token_hash == token_hash,
                        ApiTokenModel.revoked_at.is_(None),
                        or_(ApiTokenModel.expires_at.is_(None), ApiTokenModel.expires_at > used_at),
                        UserModel.account_type == AccountType.SERVICE.value,
                        UserModel.status == AccountStatus.ACTIVE.value,
                    )
                    .with_for_update(of=ApiTokenModel)
                )
            ).one_or_none()
            if row is None:
                return None
            token_model, account_model = row
            token_model.last_used_at = used_at
            await session.flush()
            return AuthenticatedApiToken(
                id=token_model.id,
                user=to_user(account_model),
                scopes=frozenset(ApiTokenScope(value) for value in token_model.scopes),
                expires_at=token_model.expires_at,
            )

    @staticmethod
    async def _get_account_model(
        session: AsyncSession,
        account_id: UUID,
        *,
        for_update: bool = False,
    ) -> UserModel | None:
        statement = select(UserModel).where(
            UserModel.id == account_id,
            UserModel.account_type == AccountType.SERVICE.value,
        )
        if for_update:
            statement = statement.with_for_update()
        return await session.scalar(statement)

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
