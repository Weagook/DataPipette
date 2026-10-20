"""Administrator-only audit-event query endpoint."""

from datetime import datetime
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Query, Request, status
from pydantic import BaseModel

from datapipette.api.dependencies.authentication import AdministratorSession
from datapipette.api.problems import ApiProblem, ProblemDetails
from datapipette.application.audit_events import (
    AuditEventFilter,
    AuditEventService,
    InvalidAuditCursorError,
)
from datapipette.domain.audit import AuditActorType, AuditEvent, AuditOutcome

router = APIRouter(prefix="/audit-events", tags=["audit events"])


class AuditEventResponse(BaseModel):
    id: UUID
    occurred_at: datetime
    actor_type: AuditActorType
    actor_id: UUID | None
    action: str
    outcome: AuditOutcome
    resource_type: str | None
    resource_id: UUID | None
    request_id: str | None
    details: dict[str, Any]


class CursorMeta(BaseModel):
    next_cursor: str | None


class AuditEventCollectionResponse(BaseModel):
    data: list[AuditEventResponse]
    meta: CursorMeta


def _service(request: Request) -> AuditEventService:
    return cast(AuditEventService, request.app.state.audit_event_service)


def _response(event: AuditEvent) -> AuditEventResponse:
    return AuditEventResponse(
        id=event.id,
        occurred_at=event.occurred_at,
        actor_type=event.actor_type,
        actor_id=event.actor_id,
        action=event.action,
        outcome=event.outcome,
        resource_type=event.resource_type,
        resource_id=event.resource_id,
        request_id=event.request_id,
        details=event.details,
    )


@router.get(
    "",
    response_model=AuditEventCollectionResponse,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": ProblemDetails},
        status.HTTP_401_UNAUTHORIZED: {"model": ProblemDetails},
        status.HTTP_403_FORBIDDEN: {"model": ProblemDetails},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ProblemDetails},
    },
)
async def list_audit_events(
    request: Request,
    session: AdministratorSession,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    actor_type: AuditActorType | None = None,
    actor_id: UUID | None = None,
    action: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    outcome: AuditOutcome | None = None,
    resource_type: Annotated[str | None, Query(min_length=1, max_length=50)] = None,
    resource_id: UUID | None = None,
    request_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
) -> AuditEventCollectionResponse:
    try:
        page = await _service(request).list_events(
            actor=session.user,
            filters=AuditEventFilter(
                actor_type=actor_type,
                actor_id=actor_id,
                action=action,
                outcome=outcome,
                resource_type=resource_type,
                resource_id=resource_id,
                request_id=request_id,
                occurred_from=occurred_from,
                occurred_to=occurred_to,
            ),
            cursor=cursor,
            limit=limit,
        )
    except InvalidAuditCursorError as exc:
        raise ApiProblem(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="invalid_cursor",
            title="Invalid pagination cursor",
            detail="The supplied audit-event pagination cursor is invalid.",
        ) from exc
    except ValueError as exc:
        raise ApiProblem(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="audit_filter_invalid",
            title="Audit filter invalid",
            detail=str(exc),
        ) from exc
    return AuditEventCollectionResponse(
        data=[_response(event) for event in page.events],
        meta=CursorMeta(next_cursor=page.next_cursor),
    )
