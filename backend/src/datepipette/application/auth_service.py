"""Local-account authentication use cases."""

import asyncio
import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from datepipette.application.authentication import (
    AuthenticationStore,
    LoginRateLimiter,
    SessionSecrets,
)
from datepipette.application.security import (
    generate_opaque_token,
    hash_opaque_token,
    hash_password,
    normalize_email,
    verify_password,
)
from datepipette.domain.audit import AuditOutcome
from datepipette.domain.identity import AccountStatus, AuthenticatedSession, InterfaceLocale, User


class InvalidCredentialsError(Exception):
    pass


class InvalidSessionError(Exception):
    pass


class InvalidCsrfError(Exception):
    pass


class BootstrapUnavailableError(Exception):
    pass


class RateLimitedError(Exception):
    def __init__(self, retry_after: int) -> None:
        super().__init__("Login rate limit exceeded")
        self.retry_after = retry_after


@dataclass(frozen=True, slots=True)
class LoginResult:
    user: User
    session_token: str
    csrf_token: str
    expires_at: datetime


def _rate_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class AuthenticationService:
    def __init__(
        self,
        store: AuthenticationStore,
        rate_limiter: LoginRateLimiter,
        dummy_password_hash: str,
        session_ttl_seconds: int,
    ) -> None:
        self._store = store
        self._rate_limiter = rate_limiter
        self._dummy_password_hash = dummy_password_hash
        self._session_ttl = timedelta(seconds=session_ttl_seconds)

    @classmethod
    async def create(
        cls,
        store: AuthenticationStore,
        rate_limiter: LoginRateLimiter,
        session_ttl_seconds: int,
    ) -> "AuthenticationService":
        dummy_hash = await asyncio.to_thread(hash_password, generate_opaque_token())
        return cls(store, rate_limiter, dummy_hash, session_ttl_seconds)

    async def bootstrap_admin(self, email: str, display_name: str, password: str) -> User:
        return await bootstrap_first_admin(self._store, email, display_name, password)

    async def login(
        self,
        *,
        email: str,
        password: str,
        client_address: str,
        request_id: str,
    ) -> LoginResult:
        normalized_email = normalize_email(email)
        account_key = _rate_key(normalized_email)
        client_key = _rate_key(client_address)
        retry_after = await self._rate_limiter.retry_after(account_key, client_key)
        if retry_after is not None:
            await self._record_login_problem(request_id, AuditOutcome.THROTTLED, "rate_limited")
            raise RateLimitedError(retry_after)

        credentials = await self._store.get_credentials(normalized_email)
        candidate_hash = (
            credentials.password_hash if credentials is not None else self._dummy_password_hash
        )
        password_valid = await asyncio.to_thread(verify_password, password, candidate_hash)
        account_valid = (
            credentials is not None
            and credentials.user.status is AccountStatus.ACTIVE
            and password_valid
        )
        if not account_valid:
            retry_after = await self._rate_limiter.record_failure(account_key, client_key)
            outcome = AuditOutcome.THROTTLED if retry_after is not None else AuditOutcome.FAILURE
            reason = "rate_limited" if retry_after is not None else "invalid_credentials"
            await self._record_login_problem(request_id, outcome, reason)
            if retry_after is not None:
                raise RateLimitedError(retry_after)
            raise InvalidCredentialsError

        assert credentials is not None
        await self._rate_limiter.reset_account(account_key)
        session_token = generate_opaque_token()
        csrf_token = generate_opaque_token()
        created_at = datetime.now(UTC)
        expires_at = created_at + self._session_ttl
        await self._store.create_login_session(
            user_id=credentials.user.id,
            secrets=SessionSecrets(
                session_token_hash=hash_opaque_token(session_token),
                csrf_token_hash=hash_opaque_token(csrf_token),
            ),
            created_at=created_at,
            expires_at=expires_at,
            request_id=request_id,
        )
        return LoginResult(
            user=credentials.user,
            session_token=session_token,
            csrf_token=csrf_token,
            expires_at=expires_at,
        )

    async def authenticate(self, session_token: str | None) -> AuthenticatedSession:
        if session_token is None or len(session_token) > 256:
            raise InvalidSessionError
        session = await self._store.get_session(hash_opaque_token(session_token))
        now = datetime.now(UTC)
        if (
            session is None
            or session.revoked_at is not None
            or session.expires_at <= now
            or session.user.status is not AccountStatus.ACTIVE
        ):
            raise InvalidSessionError
        return session

    def verify_csrf(
        self,
        session: AuthenticatedSession,
        csrf_cookie: str | None,
        csrf_header: str | None,
    ) -> None:
        if csrf_cookie is None or csrf_header is None:
            raise InvalidCsrfError
        if not hmac.compare_digest(csrf_cookie, csrf_header):
            raise InvalidCsrfError
        if not hmac.compare_digest(
            hash_opaque_token(csrf_header),
            session.csrf_token_hash,
        ):
            raise InvalidCsrfError

    async def logout(self, session: AuthenticatedSession, request_id: str) -> None:
        await self._store.revoke_session(
            session_id=session.id,
            user_id=session.user.id,
            occurred_at=datetime.now(UTC),
            request_id=request_id,
        )

    async def update_locale(
        self,
        *,
        user_id: UUID,
        locale: InterfaceLocale,
        request_id: str,
    ) -> User:
        return await self._store.update_locale(
            user_id=user_id,
            locale=locale,
            occurred_at=datetime.now(UTC),
            request_id=request_id,
        )

    async def _record_login_problem(
        self,
        request_id: str,
        outcome: AuditOutcome,
        reason: str,
    ) -> None:
        await self._store.record_login_failure(
            occurred_at=datetime.now(UTC),
            request_id=request_id,
            outcome=outcome.value,
            reason=reason,
        )


async def bootstrap_first_admin(
    store: AuthenticationStore,
    email: str,
    display_name: str,
    password: str,
) -> User:
    normalized_email = normalize_email(email)
    normalized_display_name = display_name.strip()
    if len(password) < 12:
        raise ValueError("Password must contain at least 12 characters")
    if len(password) > 1024:
        raise ValueError("Password must contain at most 1024 characters")
    if not normalized_display_name:
        raise ValueError("Display name must not be empty")
    if len(normalized_display_name) > 100:
        raise ValueError("Display name must contain at most 100 characters")
    password_digest = await asyncio.to_thread(hash_password, password)
    user = await store.create_first_admin(
        email=normalized_email,
        display_name=normalized_display_name,
        password_hash=password_digest,
        occurred_at=datetime.now(UTC),
    )
    if user is None:
        raise BootstrapUnavailableError
    return user
