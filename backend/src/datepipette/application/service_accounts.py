"""Administrator-managed service accounts and scoped opaque API tokens."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from datepipette.application.security import generate_opaque_token, hash_opaque_token
from datepipette.domain.identity import AccountStatus, SystemRole, User
from datepipette.domain.service_accounts import (
    ApiToken,
    ApiTokenScope,
    AuthenticatedApiToken,
    IssuedApiToken,
    ServiceAccount,
)

TOKEN_PREFIX = "dpp_"


class ServiceAccountNotFoundError(Exception):
    pass


class ServiceAccountAlreadyExistsError(Exception):
    pass


class ApiTokenNotFoundError(Exception):
    pass


class InvalidApiTokenError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class ServiceAccountUpdate:
    name: str | None = None
    role: SystemRole | None = None
    status: AccountStatus | None = None


class ServiceAccountStore(Protocol):
    async def create_account(
        self,
        *,
        account_id: UUID,
        name: str,
        role: SystemRole,
        actor_id: UUID,
        occurred_at: datetime,
        request_id: str,
    ) -> ServiceAccount: ...

    async def list_accounts(self, *, limit: int) -> tuple[ServiceAccount, ...]: ...
    async def get_account(self, account_id: UUID) -> ServiceAccount | None: ...
    async def update_account(
        self,
        *,
        account_id: UUID,
        update: ServiceAccountUpdate,
        actor_id: UUID,
        occurred_at: datetime,
        request_id: str,
    ) -> ServiceAccount | None: ...
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
    ) -> ApiToken | None: ...
    async def list_tokens(self, account_id: UUID) -> tuple[ApiToken, ...] | None: ...
    async def revoke_token(
        self,
        *,
        account_id: UUID,
        token_id: UUID,
        actor_id: UUID,
        occurred_at: datetime,
        request_id: str,
    ) -> bool | None: ...
    async def authenticate_token(
        self, *, token_hash: str, used_at: datetime
    ) -> AuthenticatedApiToken | None: ...


def _require_admin(actor: User) -> None:
    if actor.role is not SystemRole.ADMIN or actor.status is not AccountStatus.ACTIVE:
        raise ServiceAccountNotFoundError


def _name(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError("Name must not be empty")
    if len(normalized) > 100:
        raise ValueError("Name must contain at most 100 characters")
    return normalized


def _role(value: SystemRole) -> SystemRole:
    if value is SystemRole.ADMIN:
        raise ValueError("Service accounts cannot use the administrator role")
    return value


class ServiceAccountService:
    def __init__(self, store: ServiceAccountStore) -> None:
        self._store = store

    async def create(
        self,
        *,
        actor: User,
        name: str,
        role: SystemRole,
        request_id: str,
    ) -> ServiceAccount:
        _require_admin(actor)
        return await self._store.create_account(
            account_id=uuid4(),
            name=_name(name),
            role=_role(role),
            actor_id=actor.id,
            occurred_at=datetime.now(UTC),
            request_id=request_id,
        )

    async def list(self, *, actor: User, limit: int) -> tuple[ServiceAccount, ...]:
        _require_admin(actor)
        return await self._store.list_accounts(limit=limit)

    async def get(self, *, actor: User, account_id: UUID) -> ServiceAccount:
        _require_admin(actor)
        account = await self._store.get_account(account_id)
        if account is None:
            raise ServiceAccountNotFoundError
        return account

    async def update(
        self,
        *,
        actor: User,
        account_id: UUID,
        update: ServiceAccountUpdate,
        request_id: str,
    ) -> ServiceAccount:
        _require_admin(actor)
        normalized = ServiceAccountUpdate(
            name=_name(update.name) if update.name is not None else None,
            role=_role(update.role) if update.role is not None else None,
            status=update.status,
        )
        account = await self._store.update_account(
            account_id=account_id,
            update=normalized,
            actor_id=actor.id,
            occurred_at=datetime.now(UTC),
            request_id=request_id,
        )
        if account is None:
            raise ServiceAccountNotFoundError
        return account

    async def create_token(
        self,
        *,
        actor: User,
        account_id: UUID,
        name: str,
        scopes: frozenset[ApiTokenScope],
        expires_at: datetime | None,
        request_id: str,
    ) -> IssuedApiToken:
        _require_admin(actor)
        if not scopes:
            raise ValueError("At least one token scope is required")
        now = datetime.now(UTC)
        if expires_at is not None:
            if expires_at.tzinfo is None:
                raise ValueError("Token expiration must include a timezone")
            expires_at = expires_at.astimezone(UTC)
            if expires_at <= now:
                raise ValueError("Token expiration must be in the future")
        plaintext = TOKEN_PREFIX + generate_opaque_token()
        token = await self._store.create_token(
            token_id=uuid4(),
            account_id=account_id,
            name=_name(name),
            token_hash=hash_opaque_token(plaintext),
            scopes=scopes,
            expires_at=expires_at,
            actor_id=actor.id,
            occurred_at=now,
            request_id=request_id,
        )
        if token is None:
            raise ServiceAccountNotFoundError
        return IssuedApiToken(token, plaintext)

    async def list_tokens(self, *, actor: User, account_id: UUID) -> tuple[ApiToken, ...]:
        _require_admin(actor)
        tokens = await self._store.list_tokens(account_id)
        if tokens is None:
            raise ServiceAccountNotFoundError
        return tokens

    async def revoke_token(
        self,
        *,
        actor: User,
        account_id: UUID,
        token_id: UUID,
        request_id: str,
    ) -> None:
        _require_admin(actor)
        result = await self._store.revoke_token(
            account_id=account_id,
            token_id=token_id,
            actor_id=actor.id,
            occurred_at=datetime.now(UTC),
            request_id=request_id,
        )
        if result is None:
            raise ServiceAccountNotFoundError
        if not result:
            raise ApiTokenNotFoundError


class ApiTokenAuthenticationService:
    def __init__(self, store: ServiceAccountStore) -> None:
        self._store = store

    async def authenticate(self, plaintext: str | None) -> AuthenticatedApiToken:
        if plaintext is None or not plaintext.startswith(TOKEN_PREFIX) or len(plaintext) > 256:
            raise InvalidApiTokenError
        identity = await self._store.authenticate_token(
            token_hash=hash_opaque_token(plaintext),
            used_at=datetime.now(UTC),
        )
        if identity is None:
            raise InvalidApiTokenError
        return identity
