"""Owner-authorized dataset drafts."""

import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from datepipette.application.connection_access import (
    ConnectionAccessAuthorizer,
    ConnectionAccessDeniedError,
)
from datepipette.domain.datasets import (
    Dataset,
    DatasetDefinition,
    DatasetDraft,
    DatasetValidation,
    definition_relations,
)
from datepipette.domain.identity import AccountStatus, SystemRole, User

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


@dataclass(frozen=True, slots=True)
class DatasetCreate:
    name: str
    connection_id: UUID
    definition: DatasetDefinition


@dataclass(frozen=True, slots=True)
class DatasetPage:
    datasets: list[Dataset]
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


def _encode_cursor(dataset: Dataset) -> str:
    value = f"{dataset.created_at.isoformat()}|{dataset.id}".encode()
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
