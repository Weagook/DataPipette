"""Bounded lifecycle cleanup for private analytical objects."""

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class CleanupObjectKind(StrEnum):
    SNAPSHOT = "snapshot"
    EXPORT = "export"


@dataclass(frozen=True, slots=True)
class CleanupObject:
    kind: CleanupObjectKind
    resource_id: UUID
    storage_backend: str
    object_key: str


@dataclass(frozen=True, slots=True)
class CleanupTickResult:
    claimed_objects: int
    purged_objects: int
    failed_objects: int
    scanned_objects: int
    purged_orphans: int


@dataclass(frozen=True, slots=True)
class StoredObject:
    object_key: str
    last_modified: datetime


@dataclass(frozen=True, slots=True)
class StoredObjectPage:
    objects: tuple[StoredObject, ...]
    next_after_key: str | None


class CleanupStorage(Protocol):
    def delete(self, object_key: str) -> None: ...

    def list_page(
        self,
        prefix: str,
        *,
        after_key: str | None,
        limit: int,
    ) -> StoredObjectPage: ...


class CleanupStorageRegistry(Protocol):
    @property
    def storages(self) -> Mapping[str, CleanupStorage]: ...

    def require(self, backend: str) -> CleanupStorage: ...


class StorageCleanupStore(Protocol):
    async def claim_objects(
        self,
        *,
        occurred_at: datetime,
        snapshot_before: datetime,
        export_before: datetime,
        stale_claim_before: datetime,
        limit: int,
    ) -> tuple[CleanupObject, ...]: ...

    async def mark_purged(self, item: CleanupObject, occurred_at: datetime) -> None: ...

    async def release_claim(self, item: CleanupObject) -> None: ...

    async def get_cursor(self, storage_backend: str, prefix: str) -> str | None: ...

    async def advance_cursor(
        self,
        storage_backend: str,
        prefix: str,
        after_key: str | None,
        occurred_at: datetime,
    ) -> None: ...

    async def referenced_keys(
        self,
        storage_backend: str,
        object_keys: tuple[str, ...],
    ) -> set[str]: ...

    async def record_orphans_purged(
        self,
        storage_backend: str,
        prefix: str,
        count: int,
        occurred_at: datetime,
    ) -> None: ...


class StorageCleanupService:
    _PREFIXES = ("datasets/", "exports/")

    def __init__(
        self,
        store: StorageCleanupStore,
        storages: CleanupStorageRegistry,
        *,
        batch_size: int,
        snapshot_grace_seconds: int,
        export_grace_seconds: int,
        orphan_grace_seconds: int,
        claim_lease_seconds: int = 900,
    ) -> None:
        self._store = store
        self._storages = storages
        self._batch_size = batch_size
        self._snapshot_grace = timedelta(seconds=snapshot_grace_seconds)
        self._export_grace = timedelta(seconds=export_grace_seconds)
        self._orphan_grace = timedelta(seconds=orphan_grace_seconds)
        self._claim_lease = timedelta(seconds=claim_lease_seconds)

    async def tick(self, occurred_at: datetime | None = None) -> CleanupTickResult:
        now = occurred_at or datetime.now(UTC)
        claimed = await self._store.claim_objects(
            occurred_at=now,
            snapshot_before=now - self._snapshot_grace,
            export_before=now - self._export_grace,
            stale_claim_before=now - self._claim_lease,
            limit=self._batch_size,
        )
        purged = 0
        failed = 0
        for item in claimed:
            try:
                storage = self._storages.require(item.storage_backend)
                await asyncio.to_thread(storage.delete, item.object_key)
                await self._store.mark_purged(item, now)
                purged += 1
            except (OSError, ValueError):
                await self._store.release_claim(item)
                failed += 1

        scanned = 0
        purged_orphans = 0
        for backend, storage in self._storages.storages.items():
            for prefix in self._PREFIXES:
                cursor = await self._store.get_cursor(backend, prefix)
                try:
                    page = await asyncio.to_thread(
                        storage.list_page,
                        prefix,
                        after_key=cursor,
                        limit=self._batch_size,
                    )
                except (OSError, ValueError):
                    continue
                scanned += len(page.objects)
                old_keys = tuple(
                    item.object_key
                    for item in page.objects
                    if item.last_modified <= now - self._orphan_grace
                )
                referenced = await self._store.referenced_keys(backend, old_keys)
                orphan_count = 0
                for object_key in old_keys:
                    if object_key in referenced:
                        continue
                    try:
                        await asyncio.to_thread(storage.delete, object_key)
                    except (OSError, ValueError):
                        continue
                    orphan_count += 1
                if orphan_count:
                    await self._store.record_orphans_purged(
                        backend,
                        prefix,
                        orphan_count,
                        now,
                    )
                purged_orphans += orphan_count
                await self._store.advance_cursor(
                    backend,
                    prefix,
                    page.next_after_key,
                    now,
                )
        return CleanupTickResult(len(claimed), purged, failed, scanned, purged_orphans)
