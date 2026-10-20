"""Request and inspect durable dataset refresh runs."""

from datetime import UTC, datetime
from hashlib import sha256
from typing import Protocol
from uuid import UUID, uuid4

from datapipette.application.connection_access import (
    ConnectionAccessAuthorizer,
    ConnectionAccessDeniedError,
)
from datapipette.application.datasets import (
    DatasetNotFoundError,
    DatasetStore,
    require_dataset_owner,
)
from datapipette.domain.datasets import definition_relations
from datapipette.domain.identity import User
from datapipette.domain.refresh import RefreshCancelOutcome, RefreshRequestOutcome, RefreshRun


class RefreshRunNotFoundError(Exception):
    pass


class RefreshDraftNotValidatedError(Exception):
    pass


class RefreshDispatchError(Exception):
    pass


class RefreshRunStateConflictError(Exception):
    pass


class RefreshDispatchUnavailableError(Exception):
    def __init__(self, run_id: UUID) -> None:
        self.run_id = run_id
        super().__init__("The refresh run was persisted but could not be dispatched")


class RefreshRunStore(Protocol):
    async def request(
        self,
        *,
        dataset_id: UUID,
        actor_id: UUID,
        expected_revision: int,
        idempotency_key_hash: str,
        refresh_run_id: UUID,
        occurred_at: datetime,
        request_id: str,
    ) -> RefreshRequestOutcome | None: ...

    async def get(self, *, dataset_id: UUID, run_id: UUID) -> RefreshRun | None: ...

    async def record_dispatch_attempt(self, run_id: UUID, occurred_at: datetime) -> None: ...

    async def mark_dispatched(self, run_id: UUID, occurred_at: datetime) -> None: ...

    async def cancel(
        self,
        *,
        dataset_id: UUID,
        run_id: UUID,
        actor_id: UUID,
        occurred_at: datetime,
        request_id: str,
    ) -> tuple[RefreshCancelOutcome, RefreshRun | None]: ...


class RefreshDispatcher(Protocol):
    def dispatch(self, run_id: UUID) -> None: ...


class RefreshRunService:
    def __init__(
        self,
        store: RefreshRunStore,
        dispatcher: RefreshDispatcher,
        dataset_store: DatasetStore,
        authorizer: ConnectionAccessAuthorizer,
    ) -> None:
        self._store = store
        self._dispatcher = dispatcher
        self._dataset_store = dataset_store
        self._authorizer = authorizer

    async def request(
        self,
        *,
        actor: User,
        dataset_id: UUID,
        expected_revision: int,
        idempotency_key: str,
        request_id: str,
    ) -> RefreshRequestOutcome:
        dataset = await self._dataset_store.get(dataset_id)
        draft = await self._dataset_store.get_draft(dataset_id)
        if dataset is None or draft is None:
            raise RefreshRunNotFoundError
        try:
            require_dataset_owner(actor, dataset)
            await self._authorizer.require_relations(
                actor=actor,
                connection_id=dataset.connection_id,
                relations=definition_relations(draft.definition),
            )
        except (ConnectionAccessDeniedError, DatasetNotFoundError) as exc:
            raise RefreshRunNotFoundError from exc
        now = datetime.now(UTC)
        outcome = await self._store.request(
            dataset_id=dataset_id,
            actor_id=actor.id,
            expected_revision=expected_revision,
            idempotency_key_hash=sha256(idempotency_key.encode()).hexdigest(),
            refresh_run_id=uuid4(),
            occurred_at=now,
            request_id=request_id,
        )
        if outcome is None:
            raise RefreshRunNotFoundError
        if outcome.dispatch_required:
            await self._store.record_dispatch_attempt(outcome.run.id, datetime.now(UTC))
            try:
                self._dispatcher.dispatch(outcome.run.id)
            except Exception as exc:
                raise RefreshDispatchUnavailableError(outcome.run.id) from exc
            await self._store.mark_dispatched(outcome.run.id, datetime.now(UTC))
            refreshed = await self._store.get(dataset_id=dataset_id, run_id=outcome.run.id)
            if refreshed is None:  # pragma: no cover - persistence contract guard
                raise RuntimeError("Dispatched refresh run disappeared")
            outcome = RefreshRequestOutcome(
                run=refreshed,
                coalesced=outcome.coalesced,
                dispatch_required=False,
            )
        return outcome

    async def get(self, *, actor: User, dataset_id: UUID, run_id: UUID) -> RefreshRun:
        dataset = await self._dataset_store.get(dataset_id)
        if dataset is None:
            raise RefreshRunNotFoundError
        try:
            require_dataset_owner(actor, dataset)
        except DatasetNotFoundError as exc:
            raise RefreshRunNotFoundError from exc
        run = await self._store.get(dataset_id=dataset_id, run_id=run_id)
        if run is None:
            raise RefreshRunNotFoundError
        return run

    async def cancel(
        self,
        *,
        actor: User,
        dataset_id: UUID,
        run_id: UUID,
        request_id: str,
    ) -> RefreshRun:
        dataset = await self._dataset_store.get(dataset_id)
        if dataset is None:
            raise RefreshRunNotFoundError
        try:
            require_dataset_owner(actor, dataset)
        except DatasetNotFoundError as exc:
            raise RefreshRunNotFoundError from exc
        outcome, run = await self._store.cancel(
            dataset_id=dataset_id,
            run_id=run_id,
            actor_id=actor.id,
            occurred_at=datetime.now(UTC),
            request_id=request_id,
        )
        if outcome is RefreshCancelOutcome.NOT_FOUND or run is None:
            raise RefreshRunNotFoundError
        if outcome is RefreshCancelOutcome.CONFLICT:
            raise RefreshRunStateConflictError
        return run
