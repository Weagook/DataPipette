"""Application services for durable snapshot-bound exports."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID, uuid4

from datepipette.application.snapshot_queries import (
    DatasetSnapshotNotFoundError,
    DatasetSnapshotNotPublishedError,
    InvalidSnapshotQueryError,
    prepare_snapshot_query,
)
from datepipette.domain.exports import (
    ExportArtifact,
    ExportClaimOutcome,
    ExportDownload,
    ExportFormat,
    ExportJob,
    ExportRequestOutcome,
)
from datepipette.domain.identity import AccountStatus, SystemRole, User
from datepipette.domain.snapshot_queries import (
    ActiveSnapshotQueryContext,
    SnapshotFilterOperator,
    SnapshotQuery,
    SnapshotQueryFilter,
    SnapshotQuerySort,
    SnapshotSortDirection,
)

EXPORT_RETENTION = timedelta(hours=24)
DOWNLOAD_TOKEN_TTL = timedelta(minutes=5)


class ExportNotFoundError(Exception):
    pass


class ExportDispatchUnavailableError(Exception):
    def __init__(self, export_id: UUID) -> None:
        self.export_id = export_id


class ExportNotReadyError(Exception):
    pass


class ExportExpiredError(Exception):
    pass


class InvalidExportDownloadTokenError(Exception):
    pass


class ExportFileUnavailableError(Exception):
    pass


class ExportStore(Protocol):
    async def get_active_context(self, dataset_id: UUID) -> ActiveSnapshotQueryContext | None: ...
    async def request(
        self,
        *,
        export_id: UUID,
        dataset_id: UUID,
        snapshot_id: UUID,
        actor_id: UUID,
        format: ExportFormat,
        query_document: dict[str, Any],
        idempotency_key_hash: str,
        request_id: str,
        occurred_at: datetime,
        expires_at: datetime,
    ) -> ExportRequestOutcome | None: ...
    async def get(self, *, dataset_id: UUID, export_id: UUID) -> ExportJob | None: ...
    async def get_by_id(self, export_id: UUID) -> ExportJob | None: ...
    async def list(self, *, dataset_id: UUID, limit: int) -> tuple[ExportJob, ...]: ...
    async def record_dispatch_attempt(self, export_id: UUID, occurred_at: datetime) -> None: ...
    async def mark_dispatched(self, export_id: UUID, occurred_at: datetime) -> None: ...
    async def claim(
        self, export_id: UUID, occurred_at: datetime
    ) -> tuple[ExportClaimOutcome, ExportJob | None]: ...
    async def complete(
        self, export_id: UUID, result: "ExportExecutionResult", occurred_at: datetime
    ) -> None: ...
    async def get_context(self, job: ExportJob) -> ActiveSnapshotQueryContext | None: ...


class ExportDispatcher(Protocol):
    def dispatch(self, export_id: UUID) -> None: ...


class ExportExecutor(Protocol):
    async def execute(
        self,
        job: ExportJob,
        context: ActiveSnapshotQueryContext,
    ) -> ExportArtifact: ...


class ExportDownloadTokenCodec(Protocol):
    def encode(self, *, export_id: UUID, actor_id: UUID, expires_at: datetime) -> str: ...
    def decode(self, value: str) -> tuple[UUID, UUID, datetime]: ...


class ExportFileResolver(Protocol):
    def resolve(self, object_key: str) -> Path: ...


@dataclass(frozen=True, slots=True)
class ExportExecutionResult:
    succeeded: bool
    artifact: ExportArtifact | None = None
    error_code: str | None = None


def export_query_document(query: SnapshotQuery) -> dict[str, Any]:
    return {
        "fields": [str(value) for value in query.field_ids],
        "filters": [
            {
                "field_id": str(item.field_id),
                "operator": item.operator.value,
                "value": item.value,
                "values": list(item.values),
            }
            for item in query.filters
        ],
        "sorts": [
            {"field_id": str(item.field_id), "direction": item.direction.value}
            for item in query.sorts
        ],
    }


def export_query_from_document(value: dict[str, Any]) -> SnapshotQuery:
    try:
        return SnapshotQuery(
            field_ids=tuple(UUID(item) for item in value["fields"]),
            filters=tuple(
                SnapshotQueryFilter(
                    UUID(item["field_id"]),
                    SnapshotFilterOperator(item["operator"]),
                    item.get("value"),
                    tuple(item.get("values", [])),
                )
                for item in value.get("filters", [])
            ),
            sorts=tuple(
                SnapshotQuerySort(UUID(item["field_id"]), SnapshotSortDirection(item["direction"]))
                for item in value.get("sorts", [])
            ),
            page_size=1,
            cursor=None,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidSnapshotQueryError("Stored export query is invalid") from exc


class DatasetExportService:
    def __init__(self, store: ExportStore, dispatcher: ExportDispatcher) -> None:
        self._store = store
        self._dispatcher = dispatcher

    @staticmethod
    def _authorize(actor: User) -> None:
        if actor.role is not SystemRole.ADMIN or actor.status is not AccountStatus.ACTIVE:
            raise ExportNotFoundError

    async def request(
        self,
        *,
        actor: User,
        dataset_id: UUID,
        format: ExportFormat,
        query: SnapshotQuery,
        idempotency_key: str,
        request_id: str,
    ) -> ExportRequestOutcome:
        self._authorize(actor)
        context = await self._store.get_active_context(dataset_id)
        if context is None:
            raise DatasetSnapshotNotFoundError
        prepare_snapshot_query(context, query, require_page=False)
        now = datetime.now(UTC)
        outcome = await self._store.request(
            export_id=uuid4(),
            dataset_id=dataset_id,
            snapshot_id=context.snapshot_id,
            actor_id=actor.id,
            format=format,
            query_document=export_query_document(query),
            idempotency_key_hash=sha256(idempotency_key.encode()).hexdigest(),
            request_id=request_id,
            occurred_at=now,
            expires_at=now + EXPORT_RETENTION,
        )
        if outcome is None:
            raise DatasetSnapshotNotPublishedError
        if outcome.dispatch_required:
            await self._store.record_dispatch_attempt(outcome.job.id, datetime.now(UTC))
            try:
                self._dispatcher.dispatch(outcome.job.id)
            except Exception as exc:
                raise ExportDispatchUnavailableError(outcome.job.id) from exc
            await self._store.mark_dispatched(outcome.job.id, datetime.now(UTC))
            refreshed = await self._store.get(dataset_id=dataset_id, export_id=outcome.job.id)
            if refreshed is None:
                raise RuntimeError("Dispatched export disappeared")
            outcome = ExportRequestOutcome(refreshed, outcome.replayed, False)
        return outcome

    async def get(self, *, actor: User, dataset_id: UUID, export_id: UUID) -> ExportJob:
        self._authorize(actor)
        job = await self._store.get(dataset_id=dataset_id, export_id=export_id)
        if job is None:
            raise ExportNotFoundError
        return job

    async def list(self, *, actor: User, dataset_id: UUID, limit: int) -> tuple[ExportJob, ...]:
        self._authorize(actor)
        return await self._store.list(dataset_id=dataset_id, limit=limit)


class ExportWorkerService:
    def __init__(self, store: ExportStore, executor: ExportExecutor) -> None:
        self._store = store
        self._executor = executor

    async def execute(self, export_id: UUID) -> ExportClaimOutcome:
        outcome, job = await self._store.claim(export_id, datetime.now(UTC))
        if outcome is not ExportClaimOutcome.CLAIMED or job is None:
            return outcome
        try:
            context = await self._store.get_context(job)
            if context is None:
                result = ExportExecutionResult(False, error_code="export_snapshot_unavailable")
            else:
                artifact = await self._executor.execute(job, context)
                result = ExportExecutionResult(True, artifact=artifact)
        except Exception:
            result = ExportExecutionResult(False, error_code="export_generation_failed")
        await self._store.complete(export_id, result, datetime.now(UTC))
        return outcome


class ExportDownloadService:
    def __init__(
        self,
        store: ExportStore,
        codec: ExportDownloadTokenCodec,
        resolver: ExportFileResolver,
    ) -> None:
        self._store = store
        self._codec = codec
        self._resolver = resolver

    async def issue(
        self, *, actor: User, dataset_id: UUID, export_id: UUID
    ) -> tuple[str, datetime]:
        DatasetExportService._authorize(actor)
        job = await self._store.get(dataset_id=dataset_id, export_id=export_id)
        if job is None:
            raise ExportNotFoundError
        now = datetime.now(UTC)
        if now >= job.expires_at:
            raise ExportExpiredError
        if job.state.value != "succeeded" or job.object_key is None:
            raise ExportNotReadyError
        expires_at = min(now + DOWNLOAD_TOKEN_TTL, job.expires_at)
        return self._codec.encode(
            export_id=job.id, actor_id=actor.id, expires_at=expires_at
        ), expires_at

    async def resolve(self, token: str) -> ExportDownload:
        try:
            export_id, _actor_id, token_expiry = self._codec.decode(token)
        except (TypeError, ValueError) as exc:
            raise InvalidExportDownloadTokenError from exc
        now = datetime.now(UTC)
        if now >= token_expiry:
            raise InvalidExportDownloadTokenError
        # Dataset is intentionally not disclosed by the capability route.
        job = await self._find_by_id(export_id)
        if job is None:
            raise InvalidExportDownloadTokenError
        if now >= job.expires_at:
            raise ExportExpiredError
        if job.state.value != "succeeded" or job.object_key is None or job.content_type is None:
            raise ExportNotReadyError
        try:
            path = self._resolver.resolve(job.object_key)
        except (OSError, ValueError) as exc:
            raise ExportFileUnavailableError from exc
        return ExportDownload(
            str(path), job.content_type, f"dataset-{job.dataset_id}.{job.format.value}"
        )

    async def _find_by_id(self, export_id: UUID) -> ExportJob | None:
        return await self._store.get_by_id(export_id)
