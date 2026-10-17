"""Identity and access vocabulary."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class SystemRole(StrEnum):
    """Coarse installation-wide capability role."""

    ADMIN = "admin"
    ANALYST = "analyst"
    VIEWER = "viewer"


class AccountStatus(StrEnum):
    """Whether an account may authenticate."""

    ACTIVE = "active"
    DISABLED = "disabled"


class AccountType(StrEnum):
    HUMAN = "human"
    SERVICE = "service"


class InterfaceLocale(StrEnum):
    """Human-facing language preference for the official client."""

    EN = "en"
    RU = "ru"


@dataclass(frozen=True, slots=True)
class User:
    id: UUID
    email: str
    display_name: str
    role: SystemRole
    status: AccountStatus
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime
    account_type: AccountType = AccountType.HUMAN
    locale: InterfaceLocale = InterfaceLocale.EN


@dataclass(frozen=True, slots=True)
class UserCredentials:
    user: User
    password_hash: str


@dataclass(frozen=True, slots=True)
class AuthenticatedSession:
    id: UUID
    user: User
    csrf_token_hash: str
    expires_at: datetime
    revoked_at: datetime | None
