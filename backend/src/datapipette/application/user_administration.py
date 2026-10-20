"""Administrator-managed local user accounts."""

import asyncio
import base64
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from datapipette.application.security import hash_password, normalize_email
from datapipette.domain.identity import AccountStatus, SystemRole, User


class AdministratorRequiredError(Exception):
    pass


class UserAlreadyExistsError(Exception):
    pass


class UserNotFoundError(Exception):
    pass


class LastActiveAdministratorError(Exception):
    pass


class InvalidUserCursorError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class UserUpdate:
    display_name: str | None = None
    role: SystemRole | None = None
    status: AccountStatus | None = None


@dataclass(frozen=True, slots=True)
class UserPage:
    users: list[User]
    next_cursor: str | None


class UserAdministrationStore(Protocol):
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
    ) -> User: ...

    async def list_users(
        self,
        *,
        after_created_at: datetime | None,
        after_id: UUID | None,
        limit: int,
    ) -> list[User]: ...

    async def get_user(self, user_id: UUID) -> User | None: ...

    async def update_user(
        self,
        *,
        actor_id: UUID,
        user_id: UUID,
        update: UserUpdate,
        occurred_at: datetime,
        request_id: str,
    ) -> User | None: ...

    async def revoke_user_sessions(
        self,
        *,
        actor_id: UUID,
        user_id: UUID,
        occurred_at: datetime,
        request_id: str,
    ) -> int | None: ...


def _require_administrator(actor: User) -> None:
    if actor.role is not SystemRole.ADMIN or actor.status is not AccountStatus.ACTIVE:
        raise AdministratorRequiredError


def _validate_display_name(display_name: str) -> str:
    normalized = display_name.strip()
    if not normalized:
        raise ValueError("Display name must not be empty")
    if len(normalized) > 100:
        raise ValueError("Display name must contain at most 100 characters")
    return normalized


def _encode_cursor(user: User) -> str:
    value = f"{user.created_at.isoformat()}|{user.id}".encode()
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode_cursor(cursor: str | None) -> tuple[datetime | None, UUID | None]:
    if cursor is None:
        return None, None
    try:
        padding = "=" * (-len(cursor) % 4)
        decoded = base64.b64decode(cursor + padding, altchars=b"-_", validate=True).decode()
        created_at_raw, user_id_raw = decoded.split("|", maxsplit=1)
        created_at = datetime.fromisoformat(created_at_raw)
        if created_at.tzinfo is None:
            raise ValueError
        return created_at, UUID(user_id_raw)
    except (UnicodeDecodeError, ValueError) as exc:
        raise InvalidUserCursorError from exc


class UserAdministrationService:
    def __init__(self, store: UserAdministrationStore) -> None:
        self._store = store

    async def create_user(
        self,
        *,
        actor: User,
        email: str,
        display_name: str,
        password: str,
        role: SystemRole,
        request_id: str,
    ) -> User:
        _require_administrator(actor)
        normalized_email = normalize_email(email)
        if len(normalized_email) > 320:
            raise ValueError("Email address must contain at most 320 characters")
        normalized_display_name = _validate_display_name(display_name)
        if len(password) < 12:
            raise ValueError("Password must contain at least 12 characters")
        if len(password) > 1024:
            raise ValueError("Password must contain at most 1024 characters")
        password_digest = await asyncio.to_thread(hash_password, password)
        return await self._store.create_user(
            actor_id=actor.id,
            email=normalized_email,
            display_name=normalized_display_name,
            password_hash=password_digest,
            role=role,
            occurred_at=datetime.now(UTC),
            request_id=request_id,
        )

    async def list_users(
        self,
        *,
        actor: User,
        cursor: str | None,
        limit: int,
    ) -> UserPage:
        _require_administrator(actor)
        after_created_at, after_id = _decode_cursor(cursor)
        users = await self._store.list_users(
            after_created_at=after_created_at,
            after_id=after_id,
            limit=limit + 1,
        )
        has_more = len(users) > limit
        visible_users = users[:limit]
        next_cursor = _encode_cursor(visible_users[-1]) if has_more else None
        return UserPage(users=visible_users, next_cursor=next_cursor)

    async def get_user(self, *, actor: User, user_id: UUID) -> User:
        _require_administrator(actor)
        user = await self._store.get_user(user_id)
        if user is None:
            raise UserNotFoundError
        return user

    async def update_user(
        self,
        *,
        actor: User,
        user_id: UUID,
        update: UserUpdate,
        request_id: str,
    ) -> User:
        _require_administrator(actor)
        normalized_update = UserUpdate(
            display_name=(
                _validate_display_name(update.display_name)
                if update.display_name is not None
                else None
            ),
            role=update.role,
            status=update.status,
        )
        user = await self._store.update_user(
            actor_id=actor.id,
            user_id=user_id,
            update=normalized_update,
            occurred_at=datetime.now(UTC),
            request_id=request_id,
        )
        if user is None:
            raise UserNotFoundError
        return user

    async def revoke_user_sessions(
        self,
        *,
        actor: User,
        user_id: UUID,
        request_id: str,
    ) -> int:
        _require_administrator(actor)
        revoked_count = await self._store.revoke_user_sessions(
            actor_id=actor.id,
            user_id=user_id,
            occurred_at=datetime.now(UTC),
            request_id=request_id,
        )
        if revoked_count is None:
            raise UserNotFoundError
        return revoked_count
