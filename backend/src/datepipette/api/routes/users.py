"""Administrator-only local user management endpoints."""

from datetime import datetime
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Query, Request, status
from pydantic import (
    BaseModel,
    EmailStr,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

from datepipette.api.dependencies.authentication import (
    AdministratorCsrfSession,
    AdministratorSession,
    request_id,
)
from datepipette.api.problems import ApiProblem, ProblemDetails
from datepipette.application.user_administration import (
    InvalidUserCursorError,
    LastActiveAdministratorError,
    UserAdministrationService,
    UserAlreadyExistsError,
    UserNotFoundError,
    UserUpdate,
)
from datepipette.domain.identity import AccountStatus, InterfaceLocale, SystemRole, User

router = APIRouter(prefix="/users", tags=["users"])


class CreateUserRequest(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=100)
    password: Annotated[SecretStr, Field(min_length=12, max_length=1024)]
    role: SystemRole

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Display name must not be empty")
        return normalized


class UpdateUserRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    role: SystemRole | None = None
    status: AccountStatus | None = None

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Display name must not be empty")
        return normalized

    @model_validator(mode="after")
    def require_change(self) -> "UpdateUserRequest":
        if not self.model_fields_set or all(
            getattr(self, field_name) is None for field_name in self.model_fields_set
        ):
            raise ValueError("At least one non-null field must be provided")
        return self


class UserAdminResponse(BaseModel):
    id: UUID
    email: str
    display_name: str
    role: SystemRole
    status: AccountStatus
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime
    locale: InterfaceLocale


class CursorMeta(BaseModel):
    next_cursor: str | None


class UserCollectionResponse(BaseModel):
    data: list[UserAdminResponse]
    meta: CursorMeta


class SessionRevocationResponse(BaseModel):
    revoked_count: int


def _service(request: Request) -> UserAdministrationService:
    return cast(UserAdministrationService, request.app.state.user_administration_service)


def _user_response(user: User) -> UserAdminResponse:
    return UserAdminResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        status=user.status,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
        updated_at=user.updated_at,
        locale=user.locale,
    )


def _not_found() -> ApiProblem:
    return ApiProblem(
        status_code=status.HTTP_404_NOT_FOUND,
        code="user_not_found",
        title="User not found",
        detail="The requested user does not exist.",
    )


@router.post(
    "",
    response_model=UserAdminResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ProblemDetails},
        status.HTTP_403_FORBIDDEN: {"model": ProblemDetails},
        status.HTTP_409_CONFLICT: {"model": ProblemDetails},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ProblemDetails},
    },
)
async def create_user(
    payload: CreateUserRequest,
    request: Request,
    session: AdministratorCsrfSession,
) -> UserAdminResponse:
    try:
        user = await _service(request).create_user(
            actor=session.user,
            email=str(payload.email),
            display_name=payload.display_name,
            password=payload.password.get_secret_value(),
            role=payload.role,
            request_id=request_id(),
        )
    except UserAlreadyExistsError as exc:
        raise ApiProblem(
            status_code=status.HTTP_409_CONFLICT,
            code="user_email_conflict",
            title="Email address already in use",
            detail="A local account already uses this email address.",
        ) from exc
    return _user_response(user)


@router.get(
    "",
    response_model=UserCollectionResponse,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": ProblemDetails},
        status.HTTP_401_UNAUTHORIZED: {"model": ProblemDetails},
        status.HTTP_403_FORBIDDEN: {"model": ProblemDetails},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ProblemDetails},
    },
)
async def list_users(
    request: Request,
    session: AdministratorSession,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> UserCollectionResponse:
    try:
        page = await _service(request).list_users(
            actor=session.user,
            cursor=cursor,
            limit=limit,
        )
    except InvalidUserCursorError as exc:
        raise ApiProblem(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="invalid_cursor",
            title="Invalid pagination cursor",
            detail="The supplied user pagination cursor is invalid.",
        ) from exc
    return UserCollectionResponse(
        data=[_user_response(user) for user in page.users],
        meta=CursorMeta(next_cursor=page.next_cursor),
    )


@router.get(
    "/{user_id}",
    response_model=UserAdminResponse,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ProblemDetails},
        status.HTTP_403_FORBIDDEN: {"model": ProblemDetails},
        status.HTTP_404_NOT_FOUND: {"model": ProblemDetails},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ProblemDetails},
    },
)
async def read_user(
    user_id: UUID,
    request: Request,
    session: AdministratorSession,
) -> UserAdminResponse:
    try:
        user = await _service(request).get_user(actor=session.user, user_id=user_id)
    except UserNotFoundError as exc:
        raise _not_found() from exc
    return _user_response(user)


@router.patch(
    "/{user_id}",
    response_model=UserAdminResponse,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ProblemDetails},
        status.HTTP_403_FORBIDDEN: {"model": ProblemDetails},
        status.HTTP_404_NOT_FOUND: {"model": ProblemDetails},
        status.HTTP_409_CONFLICT: {"model": ProblemDetails},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ProblemDetails},
    },
)
async def update_user(
    user_id: UUID,
    payload: UpdateUserRequest,
    request: Request,
    session: AdministratorCsrfSession,
) -> UserAdminResponse:
    try:
        user = await _service(request).update_user(
            actor=session.user,
            user_id=user_id,
            update=UserUpdate(
                display_name=payload.display_name,
                role=payload.role,
                status=payload.status,
            ),
            request_id=request_id(),
        )
    except UserNotFoundError as exc:
        raise _not_found() from exc
    except LastActiveAdministratorError as exc:
        raise ApiProblem(
            status_code=status.HTTP_409_CONFLICT,
            code="last_active_administrator",
            title="Last active administrator required",
            detail="At least one active administrator must remain.",
        ) from exc
    return _user_response(user)


@router.delete(
    "/{user_id}/sessions",
    response_model=SessionRevocationResponse,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ProblemDetails},
        status.HTTP_403_FORBIDDEN: {"model": ProblemDetails},
        status.HTTP_404_NOT_FOUND: {"model": ProblemDetails},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ProblemDetails},
    },
)
async def revoke_user_sessions(
    user_id: UUID,
    request: Request,
    session: AdministratorCsrfSession,
) -> SessionRevocationResponse:
    try:
        count = await _service(request).revoke_user_sessions(
            actor=session.user,
            user_id=user_id,
            request_id=request_id(),
        )
    except UserNotFoundError as exc:
        raise _not_found() from exc
    return SessionRevocationResponse(revoked_count=count)
