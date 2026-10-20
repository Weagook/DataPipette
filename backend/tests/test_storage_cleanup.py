"""Bounded analytical object lifecycle cleanup behavior."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from datapipette.application.storage_cleanup import (
    CleanupObject,
    CleanupObjectKind,
    StorageCleanupService,
    StoredObject,
    StoredObjectPage,
)


class FakeStorage:
    def __init__(self, now: datetime) -> None:
        self.deleted: list[str] = []
        self.fail: set[str] = set()
        self.pages = {
            "datasets/": StoredObjectPage(
                (
                    StoredObject("datasets/referenced.parquet", now - timedelta(days=2)),
                    StoredObject("datasets/orphan.parquet", now - timedelta(days=2)),
                    StoredObject("datasets/recent.parquet", now - timedelta(minutes=5)),
                ),
                "datasets/recent.parquet",
            ),
            "exports/": StoredObjectPage((), None),
        }

    def delete(self, object_key: str) -> None:
        if object_key in self.fail:
            raise OSError("private storage failure")
        self.deleted.append(object_key)

    def list_page(self, prefix: str, *, after_key: str | None, limit: int) -> StoredObjectPage:
        assert limit == 10
        assert after_key is None
        return self.pages[prefix]


class FakeRegistry:
    def __init__(self, storage: FakeStorage) -> None:
        self.storages = {"local": storage}

    def require(self, backend: str) -> FakeStorage:
        if backend not in self.storages:
            raise OSError("backend unavailable")
        return self.storages[backend]


class FakeStore:
    def __init__(self, claimed: tuple[CleanupObject, ...]) -> None:
        self.claimed = claimed
        self.marked: list[CleanupObject] = []
        self.released: list[CleanupObject] = []
        self.cursors: dict[tuple[str, str], str | None] = {}
        self.orphan_audits: list[tuple[str, str, int]] = []

    async def claim_objects(self, **values: object) -> tuple[CleanupObject, ...]:
        assert values["limit"] == 10
        result, self.claimed = self.claimed, ()
        return result

    async def mark_purged(self, item: CleanupObject, occurred_at: datetime) -> None:
        del occurred_at
        self.marked.append(item)

    async def release_claim(self, item: CleanupObject) -> None:
        self.released.append(item)

    async def get_cursor(self, storage_backend: str, prefix: str) -> str | None:
        return self.cursors.get((storage_backend, prefix))

    async def advance_cursor(
        self,
        storage_backend: str,
        prefix: str,
        after_key: str | None,
        occurred_at: datetime,
    ) -> None:
        del occurred_at
        self.cursors[(storage_backend, prefix)] = after_key

    async def referenced_keys(self, storage_backend: str, object_keys: tuple[str, ...]) -> set[str]:
        assert storage_backend == "local"
        return {"datasets/referenced.parquet"}.intersection(object_keys)

    async def record_orphans_purged(
        self,
        storage_backend: str,
        prefix: str,
        count: int,
        occurred_at: datetime,
    ) -> None:
        del occurred_at
        self.orphan_audits.append((storage_backend, prefix, count))


def test_cleanup_purges_eligible_objects_and_old_unreferenced_objects() -> None:
    now = datetime(2026, 10, 11, tzinfo=UTC)
    successful = CleanupObject(
        CleanupObjectKind.SNAPSHOT,
        uuid4(),
        "local",
        "datasets/superseded.parquet",
    )
    failed = CleanupObject(
        CleanupObjectKind.EXPORT,
        uuid4(),
        "local",
        "exports/failed.csv",
    )
    store = FakeStore((successful, failed))
    storage = FakeStorage(now)
    storage.fail.add(failed.object_key)
    service = StorageCleanupService(
        store,
        FakeRegistry(storage),
        batch_size=10,
        snapshot_grace_seconds=3600,
        export_grace_seconds=300,
        orphan_grace_seconds=86_400,
    )

    result = asyncio.run(service.tick(now))

    assert result.claimed_objects == 2
    assert result.purged_objects == 1
    assert result.failed_objects == 1
    assert result.scanned_objects == 3
    assert result.purged_orphans == 1
    assert store.marked == [successful]
    assert store.released == [failed]
    assert storage.deleted == [successful.object_key, "datasets/orphan.parquet"]
    assert store.orphan_audits == [("local", "datasets/", 1)]
    assert store.cursors == {
        ("local", "datasets/"): "datasets/recent.parquet",
        ("local", "exports/"): None,
    }
