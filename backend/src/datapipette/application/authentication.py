"""Authentication application contracts and value objects."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from datapipette.domain.identity import (
    AuthenticatedSession,
    InterfaceLocale,
    User,
    UserCredentials,
)


@dataclass(frozen=True, slots=True)
class SessionSecrets:
    session_token_hash: str
    csrf_token_hash: str


class AuthenticationStore(Protocol):
    async def get_credentials(self, normalized_email: str) -> UserCredentials | None: ...

    async def create_first_admin(
        self,
        *,
        email: str,
        display_name: str,
        password_hash: str,
        occurred_at: datetime,
    ) -> User | None: ...

    async def create_login_session(
        self,
        *,
        user_id: UUID,
        secrets: SessionSecrets,
        created_at: datetime,
        expires_at: datetime,
        request_id: str,
    ) -> UUID: ...

    async def record_login_failure(
        self,
        *,
        occurred_at: datetime,
        request_id: str,
        outcome: str,
        reason: str,
    ) -> None: ...

    async def get_session(self, token_hash: str) -> AuthenticatedSession | None: ...

    async def revoke_session(
        self,
        *,
        session_id: UUID,
        user_id: UUID,
        occurred_at: datetime,
        request_id: str,
    ) -> None: ...

    async def update_locale(
        self,
        *,
        user_id: UUID,
        locale: InterfaceLocale,
        occurred_at: datetime,
        request_id: str,
    ) -> User: ...


class LoginRateLimiter(Protocol):
    async def retry_after(self, account_key: str, client_key: str) -> int | None: ...

    async def record_failure(self, account_key: str, client_key: str) -> int | None: ...

    async def reset_account(self, account_key: str) -> None: ...
