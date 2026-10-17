"""Local human-account session endpoints."""

from datetime import datetime
from typing import Annotated, cast

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, EmailStr, Field, SecretStr

from datepipette.api.dependencies.authentication import (
    CSRF_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    CsrfSession,
    CurrentSession,
    authentication_service,
    request_id,
)
from datepipette.api.problems import ApiProblem, ProblemDetails
from datepipette.application.auth_service import (
    AuthenticationService,
    InvalidCredentialsError,
    RateLimitedError,
)
from datepipette.config import Settings
from datepipette.domain.identity import InterfaceLocale, User

router = APIRouter(prefix="/auth", tags=["authentication"])


class LoginRequest(BaseModel):
    email: EmailStr
    password: Annotated[SecretStr, Field(min_length=1, max_length=1024)]


class UserResponse(BaseModel):
    id: str
    email: str
    display_name: str
    role: str
    locale: InterfaceLocale


class UpdateCurrentUserPreferencesRequest(BaseModel):
    locale: InterfaceLocale


class LoginResponse(BaseModel):
    user: UserResponse
    expires_at: datetime


def _user_response(user: User) -> UserResponse:
    return UserResponse(
        id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        role=user.role.value,
        locale=user.locale,
    )


def _service(request: Request) -> AuthenticationService:
    return authentication_service(request)


def _settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


@router.post(
    "/sessions",
    response_model=LoginResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ProblemDetails},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ProblemDetails},
        status.HTTP_429_TOO_MANY_REQUESTS: {"model": ProblemDetails},
    },
)
async def create_session(
    payload: LoginRequest, request: Request, response: Response
) -> LoginResponse:
    client_address = request.client.host if request.client is not None else "unknown"
    try:
        result = await _service(request).login(
            email=str(payload.email),
            password=payload.password.get_secret_value(),
            client_address=client_address,
            request_id=request_id(),
        )
    except InvalidCredentialsError as exc:
        raise ApiProblem(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="invalid_credentials",
            title="Invalid credentials",
            detail="The supplied credentials are invalid.",
        ) from exc
    except RateLimitedError as exc:
        raise ApiProblem(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            code="login_rate_limited",
            title="Too many login attempts",
            detail="Wait before trying to sign in again.",
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc

    settings = _settings(request)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        result.session_token,
        max_age=settings.session_ttl_seconds,
        path="/",
        secure=settings.effective_session_cookie_secure,
        samesite="strict",
        httponly=True,
    )
    response.set_cookie(
        CSRF_COOKIE_NAME,
        result.csrf_token,
        max_age=settings.session_ttl_seconds,
        path="/",
        secure=settings.effective_session_cookie_secure,
        samesite="strict",
        httponly=False,
    )
    response.headers["Cache-Control"] = "no-store"
    return LoginResponse(user=_user_response(result.user), expires_at=result.expires_at)


@router.get(
    "/me",
    response_model=UserResponse,
    responses={status.HTTP_401_UNAUTHORIZED: {"model": ProblemDetails}},
)
async def read_current_user(
    request: Request,
    response: Response,
    session: CurrentSession,
) -> UserResponse:
    response.headers["Cache-Control"] = "no-store"
    return _user_response(session.user)


@router.patch(
    "/me/preferences",
    response_model=UserResponse,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ProblemDetails},
        status.HTTP_403_FORBIDDEN: {"model": ProblemDetails},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ProblemDetails},
    },
)
async def update_current_user_preferences(
    payload: UpdateCurrentUserPreferencesRequest,
    request: Request,
    response: Response,
    session: CsrfSession,
) -> UserResponse:
    user = await _service(request).update_locale(
        user_id=session.user.id,
        locale=payload.locale,
        request_id=request_id(),
    )
    response.headers["Cache-Control"] = "no-store"
    return _user_response(user)


@router.delete(
    "/sessions/current",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ProblemDetails},
        status.HTTP_403_FORBIDDEN: {"model": ProblemDetails},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ProblemDetails},
    },
)
async def delete_current_session(
    request: Request,
    session: CsrfSession,
) -> Response:
    settings = _settings(request)
    await _service(request).logout(session, request_id())

    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        path="/",
        secure=settings.effective_session_cookie_secure,
        httponly=True,
        samesite="strict",
    )
    response.delete_cookie(
        CSRF_COOKIE_NAME,
        path="/",
        secure=settings.effective_session_cookie_secure,
        httponly=False,
        samesite="strict",
    )
    response.headers["Cache-Control"] = "no-store"
    return response
