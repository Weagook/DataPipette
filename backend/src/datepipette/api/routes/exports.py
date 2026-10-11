"""Durable CSV/XLSX dataset exports and private downloads."""

from dataclasses import asdict
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from datepipette.api.dependencies.authentication import (
    CsrfSession,
    CurrentSession,
    request_id,
)
from datepipette.api.problems import ApiProblem, ProblemDetails
from datepipette.api.routes.snapshot_queries import SnapshotFilterPayload, SnapshotSortPayload
from datepipette.application.exports import (
    DatasetExportService,
    ExportDispatchUnavailableError,
    ExportDownloadService,
    ExportExpiredError,
    ExportFileUnavailableError,
    ExportNotFoundError,
    ExportNotReadyError,
    InvalidExportDownloadTokenError,
)
from datepipette.application.snapshot_queries import (
    MAX_QUERY_FIELDS,
    MAX_QUERY_FILTERS,
    MAX_QUERY_SORTS,
    DatasetSnapshotNotFoundError,
    DatasetSnapshotNotPublishedError,
    InvalidSnapshotQueryError,
)
from datepipette.domain.exports import ExportFormat, ExportJob, ExportState
from datepipette.domain.snapshot_queries import (
    SnapshotQuery,
    SnapshotQueryFilter,
    SnapshotQuerySort,
)

router = APIRouter(tags=["dataset exports"])


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExportCreateRequest(StrictModel):
    format: ExportFormat
    fields: list[UUID] = Field(min_length=1, max_length=MAX_QUERY_FIELDS)
    filters: list[SnapshotFilterPayload] = Field(default_factory=list, max_length=MAX_QUERY_FILTERS)
    sorts: list[SnapshotSortPayload] = Field(default_factory=list, max_length=MAX_QUERY_SORTS)


class ExportResponse(BaseModel):
    id: UUID
    dataset_id: UUID
    snapshot_id: UUID
    requested_by: UUID
    format: ExportFormat
    state: ExportState
    fields: list[UUID]
    filter_count: int
    sort_count: int
    dispatch_attempts: int
    attempt_count: int
    row_count: int | None
    byte_size: int | None
    checksum_sha256: str | None
    error_code: str | None
    requested_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    expires_at: datetime
    updated_at: datetime


class ExportCollectionResponse(BaseModel):
    data: list[ExportResponse]
    meta: dict[str, int]


class ExportDownloadLinkResponse(BaseModel):
    download_url: str
    expires_at: datetime


def _export_service(request: Request) -> DatasetExportService:
    return cast(DatasetExportService, request.app.state.export_service)


def _download_service(request: Request) -> ExportDownloadService:
    return cast(ExportDownloadService, request.app.state.export_download_service)


def _response(job: ExportJob) -> ExportResponse:
    values = asdict(job)
    values.pop("query")
    for internal in ("dispatched_at", "object_key", "storage_backend", "content_type"):
        values.pop(internal)
    return ExportResponse(
        **values,
        fields=list(job.query.field_ids),
        filter_count=len(job.query.filters),
        sort_count=len(job.query.sorts),
    )


def _query(payload: ExportCreateRequest) -> SnapshotQuery:
    return SnapshotQuery(
        field_ids=tuple(payload.fields),
        filters=tuple(
            SnapshotQueryFilter(item.field_id, item.operator, item.value, tuple(item.values))
            for item in payload.filters
        ),
        sorts=tuple(SnapshotQuerySort(item.field_id, item.direction) for item in payload.sorts),
        page_size=1,
        cursor=None,
    )


_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ProblemDetails},
    401: {"model": ProblemDetails},
    403: {"model": ProblemDetails},
    404: {"model": ProblemDetails},
    409: {"model": ProblemDetails},
    410: {"model": ProblemDetails},
    422: {"model": ProblemDetails},
    428: {"model": ProblemDetails},
    503: {"model": ProblemDetails},
}
_DOWNLOAD_RESPONSES = {
    **_RESPONSES,
    307: {"description": "Redirect to a short-lived private object-storage URL"},
}


@router.post(
    "/datasets/{dataset_id}/exports",
    response_model=ExportResponse,
    status_code=202,
    responses=_RESPONSES,
)
async def create_export(
    dataset_id: UUID,
    payload: ExportCreateRequest,
    request: Request,
    response: Response,
    session: CsrfSession,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=200),
) -> ExportResponse:
    if not idempotency_key:
        raise ApiProblem(
            status_code=428,
            code="idempotency_key_required",
            title="Idempotency key required",
            detail="Supply a unique Idempotency-Key for this export request.",
        )
    try:
        outcome = await _export_service(request).request(
            actor=session.user,
            dataset_id=dataset_id,
            format=payload.format,
            query=_query(payload),
            idempotency_key=idempotency_key,
            request_id=request_id(),
        )
    except DatasetSnapshotNotFoundError as exc:
        raise ApiProblem(
            status_code=404,
            code="dataset_not_found",
            title="Dataset not found",
            detail="The requested dataset does not exist.",
        ) from exc
    except ExportNotFoundError as exc:
        raise ApiProblem(
            status_code=404,
            code="dataset_not_found",
            title="Dataset not found",
            detail="The requested dataset does not exist or is not accessible.",
        ) from exc
    except DatasetSnapshotNotPublishedError as exc:
        raise ApiProblem(
            status_code=409,
            code="dataset_snapshot_not_published",
            title="Dataset snapshot not published",
            detail="Publish a successful dataset snapshot before exporting rows.",
        ) from exc
    except InvalidSnapshotQueryError as exc:
        raise ApiProblem(
            status_code=422,
            code="export_query_invalid",
            title="Export query invalid",
            detail=str(exc),
        ) from exc
    except ExportDispatchUnavailableError as exc:
        location = f"/api/v1/datasets/{dataset_id}/exports/{exc.export_id}"
        raise ApiProblem(
            status_code=503,
            code="export_dispatch_unavailable",
            title="Export dispatch unavailable",
            detail="The export was saved but Redis could not accept it. Retry with the same key.",
            headers={"Location": location, "Retry-After": "5"},
        ) from exc
    location = f"/api/v1/datasets/{dataset_id}/exports/{outcome.job.id}"
    response.headers["Location"] = location
    response.headers["X-DatePipette-Idempotent-Replay"] = str(outcome.replayed).lower()
    return _response(outcome.job)


@router.get(
    "/datasets/{dataset_id}/exports", response_model=ExportCollectionResponse, responses=_RESPONSES
)
async def list_exports(
    dataset_id: UUID,
    request: Request,
    session: CurrentSession,
    limit: int = Query(default=50, ge=1, le=100),
) -> ExportCollectionResponse:
    try:
        jobs = await _export_service(request).list(
            actor=session.user, dataset_id=dataset_id, limit=limit
        )
    except ExportNotFoundError as exc:
        raise ApiProblem(
            status_code=404,
            code="dataset_not_found",
            title="Dataset not found",
            detail="The requested dataset does not exist or is not accessible.",
        ) from exc
    return ExportCollectionResponse(data=[_response(job) for job in jobs], meta={"limit": limit})


@router.get(
    "/datasets/{dataset_id}/exports/{export_id}",
    response_model=ExportResponse,
    responses=_RESPONSES,
)
async def read_export(
    dataset_id: UUID, export_id: UUID, request: Request, session: CurrentSession
) -> ExportResponse:
    try:
        return _response(
            await _export_service(request).get(
                actor=session.user, dataset_id=dataset_id, export_id=export_id
            )
        )
    except ExportNotFoundError as exc:
        raise ApiProblem(
            status_code=404,
            code="export_not_found",
            title="Export not found",
            detail="The requested export does not exist for this dataset.",
        ) from exc


@router.post(
    "/datasets/{dataset_id}/exports/{export_id}/download-link",
    response_model=ExportDownloadLinkResponse,
    responses=_RESPONSES,
)
async def issue_download_link(
    dataset_id: UUID, export_id: UUID, request: Request, session: CsrfSession
) -> ExportDownloadLinkResponse:
    try:
        token, expires_at = await _download_service(request).issue(
            actor=session.user, dataset_id=dataset_id, export_id=export_id
        )
    except ExportNotFoundError as exc:
        raise ApiProblem(
            status_code=404,
            code="export_not_found",
            title="Export not found",
            detail="The requested export does not exist for this dataset.",
        ) from exc
    except ExportNotReadyError as exc:
        raise ApiProblem(
            status_code=409,
            code="export_not_ready",
            title="Export not ready",
            detail="The export has not completed successfully.",
        ) from exc
    except ExportExpiredError as exc:
        raise ApiProblem(
            status_code=410,
            code="export_expired",
            title="Export expired",
            detail="The export retention period has ended; create a new export.",
        ) from exc
    return ExportDownloadLinkResponse(
        download_url=f"/api/v1/export-downloads?token={token}", expires_at=expires_at
    )


@router.get(
    "/export-downloads",
    response_class=FileResponse,
    response_model=None,
    responses=_DOWNLOAD_RESPONSES,
)
async def download_export(
    request: Request, token: str = Query(min_length=20, max_length=2048)
) -> FileResponse | RedirectResponse:
    try:
        download = await _download_service(request).resolve(token)
    except InvalidExportDownloadTokenError as exc:
        raise ApiProblem(
            status_code=404,
            code="export_download_not_found",
            title="Export download not found",
            detail="The download capability is invalid or expired.",
        ) from exc
    except ExportExpiredError as exc:
        raise ApiProblem(
            status_code=410,
            code="export_expired",
            title="Export expired",
            detail="The export retention period has ended; create a new export.",
        ) from exc
    except ExportNotReadyError as exc:
        raise ApiProblem(
            status_code=409,
            code="export_not_ready",
            title="Export not ready",
            detail="The export has not completed successfully.",
        ) from exc
    except ExportFileUnavailableError as exc:
        raise ApiProblem(
            status_code=503,
            code="export_file_unavailable",
            title="Export file unavailable",
            detail="The private export file could not be read.",
        ) from exc
    if download.redirect_url is not None:
        return RedirectResponse(download.redirect_url, status_code=307)
    if download.path is None:
        raise ApiProblem(
            status_code=503,
            code="export_file_unavailable",
            title="Export file unavailable",
            detail="The private export file could not be read.",
        )
    return FileResponse(download.path, media_type=download.content_type, filename=download.filename)
