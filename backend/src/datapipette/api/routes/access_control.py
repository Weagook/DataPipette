"""Group membership and dataset ACL administration endpoints."""

from dataclasses import asdict
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from datapipette.api.dependencies.authentication import (
    AdministratorCsrfSession,
    AdministratorSession,
    CsrfSession,
    CurrentSession,
    request_id,
)
from datapipette.api.problems import ApiProblem, ProblemDetails
from datapipette.application.access_control import (
    DatasetAccessDeniedError,
    DatasetGrantNotFoundError,
    DatasetGrantService,
    GrantPrincipalNotFoundError,
    GroupAlreadyExistsError,
    GroupMemberNotFoundError,
    GroupNotFoundError,
    GroupService,
)
from datapipette.domain.access_control import (
    DatasetGrant,
    DatasetPermission,
    GrantPrincipalType,
    Group,
    GroupMembership,
)

router = APIRouter(tags=["access control"])


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GroupWriteRequest(StrictModel):
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("Value must not be empty")
        return normalized


class GroupResponse(BaseModel):
    id: UUID
    name: str
    created_by: UUID
    created_at: datetime
    updated_at: datetime


class GroupCollectionResponse(BaseModel):
    data: list[GroupResponse]
    meta: dict[str, int]


class MembershipResponse(BaseModel):
    group_id: UUID
    user_id: UUID
    created_by: UUID
    created_at: datetime


class MembershipCollectionResponse(BaseModel):
    data: list[MembershipResponse]


class DatasetGrantCreateRequest(StrictModel):
    principal_type: GrantPrincipalType
    principal_id: UUID
    permission: DatasetPermission


class DatasetGrantResponse(BaseModel):
    id: UUID
    dataset_id: UUID
    principal_type: GrantPrincipalType
    principal_id: UUID
    permission: DatasetPermission
    created_by: UUID
    created_at: datetime


class DatasetGrantCollectionResponse(BaseModel):
    data: list[DatasetGrantResponse]


def _groups(request: Request) -> GroupService:
    return cast(GroupService, request.app.state.group_service)


def _grants(request: Request) -> DatasetGrantService:
    return cast(DatasetGrantService, request.app.state.dataset_grant_service)


def _group_response(value: Group) -> GroupResponse:
    return GroupResponse(**asdict(value))


def _membership_response(value: GroupMembership) -> MembershipResponse:
    return MembershipResponse(**asdict(value))


def _grant_response(value: DatasetGrant) -> DatasetGrantResponse:
    return DatasetGrantResponse(**asdict(value))


_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ProblemDetails},
    403: {"model": ProblemDetails},
    404: {"model": ProblemDetails},
    409: {"model": ProblemDetails},
    422: {"model": ProblemDetails},
}


def _group_not_found() -> ApiProblem:
    return ApiProblem(
        status_code=404,
        code="group_not_found",
        title="Group not found",
        detail="The requested group does not exist.",
    )


def _dataset_not_found() -> ApiProblem:
    return ApiProblem(
        status_code=404,
        code="dataset_not_found",
        title="Dataset not found",
        detail="The requested dataset does not exist or is not accessible.",
    )


@router.post("/groups", response_model=GroupResponse, status_code=201, responses=_RESPONSES)
async def create_group(
    payload: GroupWriteRequest,
    request: Request,
    session: AdministratorCsrfSession,
) -> GroupResponse:
    try:
        value = await _groups(request).create(
            actor=session.user, name=payload.name, request_id=request_id()
        )
    except GroupAlreadyExistsError as exc:
        raise ApiProblem(
            status_code=409,
            code="group_name_conflict",
            title="Group name already in use",
            detail="Another group already uses this name.",
        ) from exc
    return _group_response(value)


@router.get("/groups", response_model=GroupCollectionResponse, responses=_RESPONSES)
async def list_groups(
    request: Request,
    session: AdministratorSession,
    limit: int = Query(default=100, ge=1, le=100),
) -> GroupCollectionResponse:
    values = await _groups(request).list(actor=session.user, limit=limit)
    return GroupCollectionResponse(
        data=[_group_response(value) for value in values], meta={"limit": limit}
    )


@router.get("/groups/{group_id}", response_model=GroupResponse, responses=_RESPONSES)
async def read_group(
    group_id: UUID, request: Request, session: AdministratorSession
) -> GroupResponse:
    try:
        return _group_response(await _groups(request).get(actor=session.user, group_id=group_id))
    except GroupNotFoundError as exc:
        raise _group_not_found() from exc


@router.patch("/groups/{group_id}", response_model=GroupResponse, responses=_RESPONSES)
async def rename_group(
    group_id: UUID,
    payload: GroupWriteRequest,
    request: Request,
    session: AdministratorCsrfSession,
) -> GroupResponse:
    try:
        value = await _groups(request).rename(
            actor=session.user,
            group_id=group_id,
            name=payload.name,
            request_id=request_id(),
        )
    except GroupNotFoundError as exc:
        raise _group_not_found() from exc
    except GroupAlreadyExistsError as exc:
        raise ApiProblem(
            status_code=409,
            code="group_name_conflict",
            title="Group name already in use",
            detail="Another group already uses this name.",
        ) from exc
    return _group_response(value)


@router.get(
    "/groups/{group_id}/members",
    response_model=MembershipCollectionResponse,
    responses=_RESPONSES,
)
async def list_group_members(
    group_id: UUID, request: Request, session: AdministratorSession
) -> MembershipCollectionResponse:
    try:
        values = await _groups(request).list_members(actor=session.user, group_id=group_id)
    except GroupNotFoundError as exc:
        raise _group_not_found() from exc
    return MembershipCollectionResponse(data=[_membership_response(value) for value in values])


@router.put(
    "/groups/{group_id}/members/{user_id}",
    response_model=MembershipResponse,
    responses=_RESPONSES,
)
async def add_group_member(
    group_id: UUID,
    user_id: UUID,
    request: Request,
    session: AdministratorCsrfSession,
) -> MembershipResponse:
    try:
        value = await _groups(request).add_member(
            actor=session.user,
            group_id=group_id,
            user_id=user_id,
            request_id=request_id(),
        )
    except GroupMemberNotFoundError as exc:
        raise ApiProblem(
            status_code=404,
            code="group_or_user_not_found",
            title="Group or user not found",
            detail="The group or active user does not exist.",
        ) from exc
    return _membership_response(value)


@router.delete(
    "/groups/{group_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_RESPONSES,
)
async def remove_group_member(
    group_id: UUID,
    user_id: UUID,
    request: Request,
    session: AdministratorCsrfSession,
) -> Response:
    try:
        await _groups(request).remove_member(
            actor=session.user,
            group_id=group_id,
            user_id=user_id,
            request_id=request_id(),
        )
    except GroupNotFoundError as exc:
        raise _group_not_found() from exc
    except GroupMemberNotFoundError as exc:
        raise ApiProblem(
            status_code=404,
            code="group_membership_not_found",
            title="Group membership not found",
            detail="The requested membership does not exist.",
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/datasets/{dataset_id}/grants",
    response_model=DatasetGrantCollectionResponse,
    responses=_RESPONSES,
)
async def list_dataset_grants(
    dataset_id: UUID, request: Request, session: CurrentSession
) -> DatasetGrantCollectionResponse:
    try:
        values = await _grants(request).list(actor=session.user, dataset_id=dataset_id)
    except DatasetAccessDeniedError as exc:
        raise _dataset_not_found() from exc
    return DatasetGrantCollectionResponse(data=[_grant_response(value) for value in values])


@router.post(
    "/datasets/{dataset_id}/grants",
    response_model=DatasetGrantResponse,
    status_code=201,
    responses=_RESPONSES,
)
async def create_dataset_grant(
    dataset_id: UUID,
    payload: DatasetGrantCreateRequest,
    request: Request,
    session: CsrfSession,
) -> DatasetGrantResponse:
    try:
        value = await _grants(request).create(
            actor=session.user,
            dataset_id=dataset_id,
            principal_type=payload.principal_type,
            principal_id=payload.principal_id,
            permission=payload.permission,
            request_id=request_id(),
        )
    except DatasetAccessDeniedError as exc:
        raise _dataset_not_found() from exc
    except GrantPrincipalNotFoundError as exc:
        raise ApiProblem(
            status_code=404,
            code="grant_principal_not_found",
            title="Grant principal not found",
            detail="The active analyst or group does not exist.",
        ) from exc
    return _grant_response(value)


@router.delete(
    "/datasets/{dataset_id}/grants/{grant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_RESPONSES,
)
async def revoke_dataset_grant(
    dataset_id: UUID,
    grant_id: UUID,
    request: Request,
    session: CsrfSession,
) -> Response:
    try:
        await _grants(request).revoke(
            actor=session.user,
            dataset_id=dataset_id,
            grant_id=grant_id,
            request_id=request_id(),
        )
    except DatasetAccessDeniedError as exc:
        raise _dataset_not_found() from exc
    except DatasetGrantNotFoundError as exc:
        raise ApiProblem(
            status_code=404,
            code="dataset_grant_not_found",
            title="Dataset grant not found",
            detail="The requested dataset grant does not exist.",
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
