"""Administrator-managed single-table visual dataset drafts."""

import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from datepipette.domain.datasets import Dataset, DatasetDraft, VisualDatasetDefinition
from datepipette.domain.identity import AccountStatus, SystemRole, User

MAX_DEFINITION_BYTES = 262_144


class DatasetAdministratorRequiredError(Exception):
    pass


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
    definition: VisualDatasetDefinition


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


def _require_admin(actor: User) -> None:
    if actor.role is not SystemRole.ADMIN or actor.status is not AccountStatus.ACTIVE:
        raise DatasetAdministratorRequiredError


def _name(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("Dataset name must not be empty")
    if len(normalized) > 100:
        raise ValueError("Dataset name must contain at most 100 characters")
    return normalized


def _definition_document(definition: VisualDatasetDefinition) -> dict[str, object]:
    document = definition.to_document()
    encoded = json.dumps(
        document,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    if len(encoded) > MAX_DEFINITION_BYTES:
        raise ValueError("Visual definition exceeds the maximum serialized size")
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
    def __init__(self, store: DatasetStore) -> None:
        self._store = store

    async def create(
        self,
        *,
        actor: User,
        value: DatasetCreate,
        request_id: str,
    ) -> tuple[Dataset, DatasetDraft]:
        _require_admin(actor)
        normalized = DatasetCreate(
            name=_name(value.name),
            connection_id=value.connection_id,
            definition=value.definition,
        )
        definition = _definition_document(value.definition)
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
        _require_admin(actor)
        after_created_at, after_id = _decode_cursor(cursor)
        items = await self._store.list(
            after_created_at=after_created_at,
            after_id=after_id,
            limit=limit + 1,
        )
        visible = items[:limit]
        return DatasetPage(
            datasets=visible,
            next_cursor=_encode_cursor(visible[-1]) if len(items) > limit else None,
        )

    async def get(self, *, actor: User, dataset_id: UUID) -> Dataset:
        _require_admin(actor)
        dataset = await self._store.get(dataset_id)
        if dataset is None:
            raise DatasetNotFoundError
        return dataset

    async def get_draft(self, *, actor: User, dataset_id: UUID) -> DatasetDraft:
        _require_admin(actor)
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
        _require_admin(actor)
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
        definition: VisualDatasetDefinition,
        request_id: str,
    ) -> DatasetDraft:
        _require_admin(actor)
        if expected_revision < 1:
            raise ValueError("Expected revision must be positive")
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
