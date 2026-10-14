"""Authentication, authorization, and CSRF dependencies."""

from typing import Annotated, cast

from fastapi import Depends, Header, Request, Security, status
from fastapi.security import APIKeyCookie, HTTPAuthorizationCredentials, HTTPBearer

from datepipette.api.problems import ApiProblem
from datepipette.application.auth_service import (
    AuthenticationService,
    InvalidCsrfError,
    InvalidSessionError,
)
from datepipette.application.service_accounts import (
    ApiTokenAuthenticationService,
    InvalidApiTokenError,
)
from datepipette.domain.identity import AuthenticatedSession, SystemRole
from datepipette.domain.service_accounts import (
    ApiTokenScope,
    AuthenticatedApiToken,
)
from datepipette.logging import request_id_context

SESSION_COOKIE_NAME = "datepipette_session"
CSRF_COOKIE_NAME = "datepipette_csrf"
session_cookie_scheme = APIKeyCookie(name=SESSION_COOKIE_NAME, auto_error=False)
bearer_scheme = HTTPBearer(auto_error=False)


def authentication_service(request: Request) -> AuthenticationService:
    """Return the process authentication service."""

    return cast(AuthenticationService, request.app.state.auth_service)


def request_id() -> str:
    """Return the request correlation identifier for durable audit events."""

    return request_id_context.get() or "unavailable"


async def require_current_session(
    request: Request,
    session_token: Annotated[str | None, Security(session_cookie_scheme)],
) -> AuthenticatedSession:
    """Resolve a live browser session or return a safe authentication problem."""

    try:
        return await authentication_service(request).authenticate(session_token)
    except InvalidSessionError as exc:
        raise ApiProblem(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="authentication_required",
            title="Authentication required",
            detail="A valid browser session is required.",
        ) from exc


CurrentSession = Annotated[AuthenticatedSession, Depends(require_current_session)]

AuthenticatedIdentity = AuthenticatedSession | AuthenticatedApiToken


def api_token_authentication_service(request: Request) -> ApiTokenAuthenticationService:
    return cast(
        ApiTokenAuthenticationService,
        request.app.state.api_token_authentication_service,
    )


async def require_dashboard_reader(
    request: Request,
    bearer: Annotated[HTTPAuthorizationCredentials | None, Security(bearer_scheme)],
    session_token: Annotated[str | None, Security(session_cookie_scheme)],
) -> AuthenticatedIdentity:
    """Accept a browser session or a dashboard-scoped service token."""

    if bearer is not None:
        try:
            identity = await api_token_authentication_service(request).authenticate(
                bearer.credentials
            )
        except InvalidApiTokenError as exc:
            raise ApiProblem(
                status_code=status.HTTP_401_UNAUTHORIZED,
                code="authentication_required",
                title="Authentication required",
                detail="A valid browser session or API token is required.",
            ) from exc
        if ApiTokenScope.DASHBOARDS_READ not in identity.scopes:
            raise ApiProblem(
                status_code=status.HTTP_403_FORBIDDEN,
                code="api_token_scope_required",
                title="API token scope required",
                detail="The API token does not permit dashboard reads.",
            )
        return identity
    try:
        return await authentication_service(request).authenticate(session_token)
    except InvalidSessionError as exc:
        raise ApiProblem(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="authentication_required",
            title="Authentication required",
            detail="A valid browser session or API token is required.",
        ) from exc


DashboardReader = Annotated[AuthenticatedIdentity, Depends(require_dashboard_reader)]


async def require_csrf_session(
    request: Request,
    session: CurrentSession,
    csrf_header: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> AuthenticatedSession:
    """Require a session-bound CSRF cookie/header pair."""

    csrf_cookie = request.cookies.get(CSRF_COOKIE_NAME)
    try:
        authentication_service(request).verify_csrf(session, csrf_cookie, csrf_header)
    except InvalidCsrfError as exc:
        raise ApiProblem(
            status_code=status.HTTP_403_FORBIDDEN,
            code="csrf_validation_failed",
            title="CSRF validation failed",
            detail="A matching CSRF cookie and header are required.",
        ) from exc
    return session


CsrfSession = Annotated[AuthenticatedSession, Depends(require_csrf_session)]


def require_administrator(session: CurrentSession) -> AuthenticatedSession:
    """Reject authenticated principals without the administrator role."""

    if session.user.role is not SystemRole.ADMIN:
        raise ApiProblem(
            status_code=status.HTTP_403_FORBIDDEN,
            code="administrator_required",
            title="Administrator role required",
            detail="This operation is available only to administrators.",
        )
    return session


AdministratorSession = Annotated[AuthenticatedSession, Depends(require_administrator)]


def require_administrator_csrf(session: CsrfSession) -> AuthenticatedSession:
    """Require both administrator authorization and CSRF validation."""

    if session.user.role is not SystemRole.ADMIN:
        raise ApiProblem(
            status_code=status.HTTP_403_FORBIDDEN,
            code="administrator_required",
            title="Administrator role required",
            detail="This operation is available only to administrators.",
        )
    return session


AdministratorCsrfSession = Annotated[
    AuthenticatedSession,
    Depends(require_administrator_csrf),
]
