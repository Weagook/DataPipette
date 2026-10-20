"""Owner-authorized dataset drafts."""

import base64
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from datapipette.application.connection_access import (
    ConnectionAccessAuthorizer,
    ConnectionAccessDeniedError,
)
from datapipette.domain.dataset_impact import (
    DatasetDependencyReference,
    DatasetSchemaImpact,
    compare_dataset_schema,
)
from datapipette.domain.datasets import (
    Dataset,
    DatasetDefinition,
    DatasetDraft,
    DatasetValidation,
    DatasetVersion,
    DatasetVersionState,
    definition_relations,
)
from datapipette.domain.identity import AccountStatus, SystemRole, User

MAX_DEFINITION_BYTES = 262_144


class DatasetAlreadyExistsError(Exception):
    pass


class DatasetNotFoundError(Exception):
    pass


class DatasetConnectionNotFoundError(Exception):
    pass


class DatasetRevisionConflictError(Exception):
    pass


class InvalidDatasetCursorError(Exception):
    pass


class DatasetDraftImpactNotValidatedError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class DatasetCreate:
    name: str
    connection_id: UUID
    definition: DatasetDefinition


@dataclass(frozen=True, slots=True)
class DatasetPage:
    datasets: list[Dataset]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class DatasetVersionPage:
    versions: list[DatasetVersion]
    next_cursor: str | None


class DatasetStore(Protocol):
    async def connection_exists(self, connection_id: UUID) -> bool: ...

    async def create(
        self,
        *,
        dataset_id: UUID,
        draft_id: UUID,
        actor_id: UUID,
        value: DatasetCreate,
        definition: dict[str, object],
        occurred_at: datetime,
        request_id: str,
    ) -> tuple[Dataset, DatasetDraft]: ...

    async def list(
        self,
        *,
        after_created_at: datetime | None,
        after_id: UUID | None,
        owner_id: UUID | None,
        limit: int,
    ) -> list[Dataset]: ...

    async def get(self, dataset_id: UUID) -> Dataset | None: ...

    async def get_draft(self, dataset_id: UUID) -> DatasetDraft | None: ...

    async def list_versions(
        self,
        *,
        dataset_id: UUID,
        before_created_at: datetime | None,
        before_id: UUID | None,
        limit: int,
    ) -> Sequence[DatasetVersion]: ...

    async def get_version(self, *, dataset_id: UUID, version_id: UUID) -> DatasetVersion | None: ...

    async def list_chart_dependencies(
        self, dataset_id: UUID
    ) -> dict[UUID, tuple[DatasetDependencyReference, ...]]: ...

    async def rename(
        self,
        *,
        dataset_id: UUID,
        actor_id: UUID,
        name: str,
        occurred_at: datetime,
        request_id: str,
    ) -> Dataset | None: ...

    async def replace_draft(
        self,
        *,
        dataset_id: UUID,
        actor_id: UUID,
        expected_revision: int,
        definition: dict[str, object],
        occurred_at: datetime,
        request_id: str,
    ) -> DatasetDraft | None: ...

    async def record_validation(
        self,
        *,
        dataset_id: UUID,
        actor_id: UUID,
        expected_revision: int,
        validation: DatasetValidation,
        request_id: str,
    ) -> DatasetDraft | None: ...

    async def record_validation_failure(
        self,
        *,
        dataset_id: UUID,
        actor_id: UUID,
        attempted_revision: int,
        request_id: str,
        occurred_at: datetime,
    ) -> None: ...

    async def record_preview(
        self,
        *,
        dataset_id: UUID,
        actor_id: UUID,
        expected_revision: int,
        requested_limit: int,
        returned_rows: int,
        truncation_reason: str | None,
        request_id: str,
        occurred_at: datetime,
    ) -> bool: ...

    async def record_preview_failure(
        self,
        *,
        dataset_id: UUID,
        actor_id: UUID,
        attempted_revision: int,
        request_id: str,
        occurred_at: datetime,
    ) -> None: ...

    async def record_impact_analysis(
        self,
        *,
        dataset_id: UUID,
        actor_id: UUID,
        impact: DatasetSchemaImpact,
        expected_validated_at: datetime,
        request_id: str,
        occurred_at: datetime,
    ) -> bool: ...


def require_dataset_owner(actor: User, dataset: Dataset) -> None:
    if actor.status is not AccountStatus.ACTIVE:
        raise DatasetNotFoundError
    if actor.role is SystemRole.ADMIN:
        return
    if actor.role is SystemRole.ANALYST and dataset.owner_id == actor.id:
        return
    raise DatasetNotFoundError


def _name(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("Dataset name must not be empty")
    if len(normalized) > 100:
        raise ValueError("Dataset name must contain at most 100 characters")
    return normalized


def _definition_document(definition: DatasetDefinition) -> dict[str, object]:
    document = definition.to_document()
    encoded = json.dumps(
        document,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    if len(encoded) > MAX_DEFINITION_BYTES:
        raise ValueError("Dataset definition exceeds the maximum serialized size")
    return document


def _encode_position(created_at: datetime, identifier: UUID) -> str:
    value = f"{created_at.isoformat()}|{identifier}".encode()
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode_cursor(value: str | None) -> tuple[datetime | None, UUID | None]:
    if value is None:
        return None, None
    try:
        padding = "=" * (-len(value) % 4)
        decoded = base64.b64decode(value + padding, altchars=b"-_", validate=True).decode()
        timestamp, identifier = decoded.split("|", maxsplit=1)
        created_at = datetime.fromisoformat(timestamp)
        if created_at.tzinfo is None:
            raise ValueError
        return created_at, UUID(identifier)
    except (UnicodeDecodeError, ValueError) as exc:
        raise InvalidDatasetCursorError from exc


def _encode_cursor(dataset: Dataset) -> str:
    return _encode_position(dataset.created_at, dataset.id)


class DatasetService:
    def __init__(self, store: DatasetStore, authorizer: ConnectionAccessAuthorizer) -> None:
        self._store = store
        self._authorizer = authorizer

    async def create(
        self,
        *,
        actor: User,
        value: DatasetCreate,
        request_id: str,
    ) -> tuple[Dataset, DatasetDraft]:
        if actor.status is not AccountStatus.ACTIVE or actor.role not in {
            SystemRole.ADMIN,
            SystemRole.ANALYST,
        }:
            raise DatasetConnectionNotFoundError
        normalized = DatasetCreate(
            name=_name(value.name),
            connection_id=value.connection_id,
            definition=value.definition,
        )
        definition = _definition_document(value.definition)
        try:
            await self._authorizer.require_relations(
                actor=actor,
                connection_id=value.connection_id,
                relations=definition_relations(value.definition),
            )
        except ConnectionAccessDeniedError as exc:
            raise DatasetConnectionNotFoundError from exc
        if not await self._store.connection_exists(value.connection_id):
            raise DatasetConnectionNotFoundError
        return await self._store.create(
            dataset_id=uuid4(),
            draft_id=uuid4(),
            actor_id=actor.id,
            value=normalized,
            definition=definition,
            occurred_at=datetime.now(UTC),
            request_id=request_id,
        )

    async def list(self, *, actor: User, cursor: str | None, limit: int) -> DatasetPage:
        if actor.status is not AccountStatus.ACTIVE or actor.role is SystemRole.VIEWER:
            return DatasetPage([], None)
        after_created_at, after_id = _decode_cursor(cursor)
        items = await self._store.list(
            after_created_at=after_created_at,
            after_id=after_id,
            owner_id=None if actor.role is SystemRole.ADMIN else actor.id,
            limit=limit + 1,
        )
        visible = items[:limit]
        return DatasetPage(
            datasets=visible,
            next_cursor=_encode_cursor(visible[-1]) if len(items) > limit else None,
        )

    async def get(self, *, actor: User, dataset_id: UUID) -> Dataset:
        dataset = await self._store.get(dataset_id)
        if dataset is None:
            raise DatasetNotFoundError
        require_dataset_owner(actor, dataset)
        return dataset

    async def get_draft(self, *, actor: User, dataset_id: UUID) -> DatasetDraft:
        dataset = await self._store.get(dataset_id)
        if dataset is None:
            raise DatasetNotFoundError
        require_dataset_owner(actor, dataset)
        draft = await self._store.get_draft(dataset_id)
        if draft is None:
            raise DatasetNotFoundError
        return draft

    async def list_versions(
        self,
        *,
        actor: User,
        dataset_id: UUID,
        cursor: str | None,
        limit: int,
    ) -> DatasetVersionPage:
        dataset = await self._store.get(dataset_id)
        if dataset is None:
            raise DatasetNotFoundError
        require_dataset_owner(actor, dataset)
        before_created_at, before_id = _decode_cursor(cursor)
        items = await self._store.list_versions(
            dataset_id=dataset_id,
            before_created_at=before_created_at,
            before_id=before_id,
            limit=limit + 1,
        )
        visible = items[:limit]
        return DatasetVersionPage(
            versions=list(visible),
            next_cursor=(
                _encode_position(visible[-1].created_at, visible[-1].id)
                if len(items) > limit
                else None
            ),
        )

    async def get_version(
        self,
        *,
        actor: User,
        dataset_id: UUID,
        version_id: UUID,
    ) -> DatasetVersion:
        dataset = await self._store.get(dataset_id)
        if dataset is None:
            raise DatasetNotFoundError
        require_dataset_owner(actor, dataset)
        version = await self._store.get_version(dataset_id=dataset_id, version_id=version_id)
        if version is None or version.state is DatasetVersionState.DRAFT:
            raise DatasetNotFoundError
        return version

    async def analyze_draft_impact(
        self,
        *,
        actor: User,
        dataset_id: UUID,
        expected_revision: int,
        request_id: str,
    ) -> DatasetSchemaImpact:
        dataset = await self._store.get(dataset_id)
        draft = await self._store.get_draft(dataset_id)
        if dataset is None or draft is None:
            raise DatasetNotFoundError
        require_dataset_owner(actor, dataset)
        try:
            await self._authorizer.require_relations(
                actor=actor,
                connection_id=dataset.connection_id,
                relations=definition_relations(draft.definition),
            )
        except ConnectionAccessDeniedError as exc:
            raise DatasetNotFoundError from exc
        if draft.revision != expected_revision:
            raise DatasetRevisionConflictError
        if draft.validation is None or draft.validation.validated_revision != draft.revision:
            raise DatasetDraftImpactNotValidatedError
        baseline = None
        if dataset.active_version_id is not None:
            baseline = await self._store.get_version(
                dataset_id=dataset_id,
                version_id=dataset.active_version_id,
            )
            if baseline is None:  # pragma: no cover - foreign-key invariant
                raise RuntimeError("Active dataset version disappeared")
        dependencies = await self._store.list_chart_dependencies(dataset_id)
        impact = compare_dataset_schema(
            dataset_id=dataset_id,
            baseline=baseline,
            draft=draft,
            dependencies=dependencies,
        )
        recorded = await self._store.record_impact_analysis(
            dataset_id=dataset_id,
            actor_id=actor.id,
            impact=impact,
            expected_validated_at=draft.validation.validated_at,
            request_id=request_id,
            occurred_at=datetime.now(UTC),
        )
        if not recorded:
            raise DatasetRevisionConflictError
        return impact

    async def rename(
        self,
        *,
        actor: User,
        dataset_id: UUID,
        name: str,
        request_id: str,
    ) -> Dataset:
        current = await self._store.get(dataset_id)
        if current is None:
            raise DatasetNotFoundError
        require_dataset_owner(actor, current)
        dataset = await self._store.rename(
            dataset_id=dataset_id,
            actor_id=actor.id,
            name=_name(name),
            occurred_at=datetime.now(UTC),
            request_id=request_id,
        )
        if dataset is None:
            raise DatasetNotFoundError
        return dataset

    async def replace_draft(
        self,
        *,
        actor: User,
        dataset_id: UUID,
        expected_revision: int,
        definition: DatasetDefinition,
        request_id: str,
    ) -> DatasetDraft:
        dataset = await self._store.get(dataset_id)
        if dataset is None:
            raise DatasetNotFoundError
        require_dataset_owner(actor, dataset)
        if expected_revision < 1:
            raise ValueError("Expected revision must be positive")
        try:
            await self._authorizer.require_relations(
                actor=actor,
                connection_id=dataset.connection_id,
                relations=definition_relations(definition),
            )
        except ConnectionAccessDeniedError as exc:
            raise DatasetNotFoundError from exc
        draft = await self._store.replace_draft(
            dataset_id=dataset_id,
            actor_id=actor.id,
            expected_revision=expected_revision,
            definition=_definition_document(definition),
            occurred_at=datetime.now(UTC),
            request_id=request_id,
        )
        if draft is None:
            raise DatasetNotFoundError
        return draft
