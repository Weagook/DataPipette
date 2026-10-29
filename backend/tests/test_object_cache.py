"""Verified local cache for immutable S3-compatible snapshot objects."""

import hashlib
import os
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from datapipette.config import Settings
from datapipette.infrastructure.object_storage import (
    ObjectStorageUnavailableError,
    S3ObjectStorage,
    VerifiedObjectCache,
    create_object_storage_registry,
)


class CountingS3Client:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.downloads: list[str] = []
        self.delay = 0.0

    def download_file(self, bucket: str, key: str, filename: str, *, Config: Any) -> None:
        del Config
        assert bucket == "analytics"
        self.downloads.append(key)
        time.sleep(self.delay)
        Path(filename).write_bytes(self.objects[key])


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _storage(tmp_path: Path, *, max_bytes: int = 1024) -> tuple[S3ObjectStorage, CountingS3Client]:
    client = CountingS3Client()
    cache = VerifiedObjectCache(tmp_path / "cache", max_bytes=max_bytes, min_idle_seconds=60)
    storage = S3ObjectStorage(
        client=client,
        bucket="analytics",
        work_root=tmp_path / "work",
        server_side_encryption=None,
        cache=cache,
    )
    return storage, client


def _read(storage: S3ObjectStorage, key: str, body: bytes) -> bytes:
    with storage.materialize(
        key,
        expected_byte_size=len(body),
        expected_checksum_sha256=_digest(body),
    ) as path:
        return path.read_bytes()


def test_cached_snapshot_is_downloaded_and_verified_once(tmp_path: Path) -> None:
    storage, client = _storage(tmp_path)
    client.objects["datasets/a.parquet"] = b"snapshot-a"

    assert _read(storage, "datasets/a.parquet", b"snapshot-a") == b"snapshot-a"
    assert _read(storage, "datasets/a.parquet", b"snapshot-a") == b"snapshot-a"

    assert client.downloads == ["datasets/a.parquet"]
    assert not list((tmp_path / "cache" / ".incoming").iterdir())
    assert not (tmp_path / "work").exists()


def test_corrupt_download_is_rejected_and_never_cached(tmp_path: Path) -> None:
    storage, client = _storage(tmp_path)
    client.objects["datasets/a.parquet"] = b"tampered!!"

    with pytest.raises(ObjectStorageUnavailableError, match="checksum"):
        _read(storage, "datasets/a.parquet", b"snapshot-a")

    assert client.downloads == ["datasets/a.parquet"]
    assert not list((tmp_path / "cache").glob("??/*.object"))
    client.objects["datasets/a.parquet"] = b"snapshot-a"
    assert _read(storage, "datasets/a.parquet", b"snapshot-a") == b"snapshot-a"


def test_truncated_cache_entry_is_replaced(tmp_path: Path) -> None:
    storage, client = _storage(tmp_path)
    client.objects["datasets/a.parquet"] = b"snapshot-a"
    _read(storage, "datasets/a.parquet", b"snapshot-a")
    entry = next((tmp_path / "cache").glob("??/*.object"))
    entry.write_bytes(b"short")

    assert _read(storage, "datasets/a.parquet", b"snapshot-a") == b"snapshot-a"
    assert len(client.downloads) == 2


def test_concurrent_first_readers_share_one_download(tmp_path: Path) -> None:
    storage, client = _storage(tmp_path)
    client.objects["datasets/a.parquet"] = b"snapshot-a"
    client.delay = 0.2
    results: list[bytes] = []

    def read() -> None:
        results.append(_read(storage, "datasets/a.parquet", b"snapshot-a"))

    threads = [threading.Thread(target=read) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results == [b"snapshot-a"] * 4
    assert client.downloads == ["datasets/a.parquet"]


def test_eviction_removes_idle_entries_but_keeps_recent_ones(tmp_path: Path) -> None:
    storage, client = _storage(tmp_path, max_bytes=20)
    bodies = {f"datasets/{name}.parquet": name.encode() * 10 for name in ("a", "b", "c")}
    client.objects.update(bodies)
    for key, body in list(bodies.items())[:2]:
        _read(storage, key, body)
    stale = next(
        path
        for path in (tmp_path / "cache").glob("??/*.object")
        if path.read_bytes() == bodies["datasets/a.parquet"]
    )
    old = time.time() - 3600
    os.utime(stale, (old, old))

    _read(storage, "datasets/c.parquet", bodies["datasets/c.parquet"])

    remaining = {path.read_bytes() for path in (tmp_path / "cache").glob("??/*.object")}
    assert remaining == {bodies["datasets/b.parquet"], bodies["datasets/c.parquet"]}


def test_recent_entries_may_exceed_the_budget_rather_than_vanish_under_readers(
    tmp_path: Path,
) -> None:
    storage, client = _storage(tmp_path, max_bytes=20)
    bodies = {f"datasets/{name}.parquet": name.encode() * 10 for name in ("a", "b", "c")}
    client.objects.update(bodies)
    for key, body in bodies.items():
        _read(storage, key, body)

    assert len(list((tmp_path / "cache").glob("??/*.object"))) == 3


def test_uncacheable_reads_use_temporary_copies(tmp_path: Path) -> None:
    storage, client = _storage(tmp_path, max_bytes=4)
    client.objects["datasets/large.parquet"] = b"larger than budget"

    for _ in range(2):
        with storage.materialize(
            "datasets/large.parquet",
            expected_byte_size=18,
            expected_checksum_sha256=_digest(b"larger than budget"),
        ) as path:
            temporary = path
            assert path.read_bytes() == b"larger than budget"
        assert not temporary.exists()
    with storage.materialize(
        "datasets/large.parquet",
        expected_byte_size=None,
        expected_checksum_sha256=None,
    ) as path:
        assert path.read_bytes() == b"larger than budget"

    assert len(client.downloads) == 3
    assert not list((tmp_path / "cache").glob("??/*.object"))


def test_unavailable_cache_volume_falls_back_to_temporary_copies(tmp_path: Path) -> None:
    storage, client = _storage(tmp_path)
    client.objects["datasets/a.parquet"] = b"snapshot-a"
    (tmp_path / "cache").write_text("not a directory")

    assert _read(storage, "datasets/a.parquet", b"snapshot-a") == b"snapshot-a"
    assert _read(storage, "datasets/a.parquet", b"snapshot-a") == b"snapshot-a"
    assert len(client.downloads) == 2


def test_cache_rejects_invalid_budgets_and_checksums(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="budget"):
        VerifiedObjectCache(tmp_path, max_bytes=0)
    cache = VerifiedObjectCache(tmp_path, max_bytes=10)
    assert not cache.accepts(byte_size=5, checksum_sha256="../escape")
    assert not cache.accepts(byte_size=11, checksum_sha256=_digest(b"x"))
    assert cache.accepts(byte_size=10, checksum_sha256=_digest(b"x"))


def test_registry_configures_or_disables_the_cache(tmp_path: Path) -> None:
    def settings(**values: Any) -> Settings:
        return Settings(
            environment="test",
            object_storage_backend="s3",
            s3_bucket="analytics",
            s3_access_key_id="access",
            s3_secret_access_key="secret",
            snapshot_storage_path=tmp_path / "work",
            **values,
        )

    enabled = create_object_storage_registry(
        settings(snapshot_cache_path=tmp_path / "cache", snapshot_cache_max_mb=2)
    ).default
    disabled = create_object_storage_registry(settings(snapshot_cache_max_mb=0)).default
    defaulted = create_object_storage_registry(settings()).default

    assert isinstance(enabled, S3ObjectStorage)
    assert enabled._cache is not None
    assert enabled._cache.root == (tmp_path / "cache").resolve()
    assert enabled._cache.max_bytes == 2 * 1024 * 1024
    assert isinstance(disabled, S3ObjectStorage)
    assert disabled._cache is None
    assert isinstance(defaulted, S3ObjectStorage)
    assert defaulted._cache is not None
    assert defaulted._cache.root == (tmp_path / "work" / ".object-cache").resolve()
