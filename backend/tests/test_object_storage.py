"""Private local and S3-compatible object storage contracts."""

import asyncio
import hashlib
import os
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

from datepipette.application.query_safety import InteractiveQueryRuntime
from datepipette.config import Settings
from datepipette.domain.exports import ExportFormat, ExportJob, ExportState
from datepipette.domain.snapshot_queries import (
    ActiveSnapshotQueryContext,
    SnapshotQuery,
    SnapshotQueryField,
    SnapshotRefreshState,
)
from datepipette.infrastructure.duckdb_runtime import DuckDbQueryConcurrencyLimiter
from datepipette.infrastructure.duckdb_snapshot_queries import DuckDbSnapshotQueryExecutor
from datepipette.infrastructure.local_exports import DatasetExportExecutor
from datepipette.infrastructure.object_storage import (
    LocalObjectStorage,
    ObjectExportDownloadResolver,
    ObjectStorageRegistry,
    ObjectStorageUnavailableError,
    S3ObjectStorage,
    create_object_storage_registry,
)


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.metadata: dict[tuple[str, str], dict[str, str]] = {}
        self.modified: dict[tuple[str, str], datetime] = {}

    def upload_file(
        self,
        filename: str,
        bucket: str,
        key: str,
        *,
        ExtraArgs: dict[str, Any],
        Config: Any,
    ) -> None:
        del Config
        identity = (bucket, key)
        self.objects[identity] = Path(filename).read_bytes()
        self.metadata[identity] = dict(ExtraArgs["Metadata"])
        self.modified[identity] = datetime.now(UTC)

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        identity = (Bucket, Key)
        return {
            "ContentLength": len(self.objects[identity]),
            "Metadata": self.metadata[identity],
        }

    def download_file(
        self,
        bucket: str,
        key: str,
        filename: str,
        *,
        Config: Any,
    ) -> None:
        del Config
        Path(filename).write_bytes(self.objects[(bucket, key)])

    def generate_presigned_url(
        self,
        operation: str,
        *,
        Params: dict[str, str],
        ExpiresIn: int,
    ) -> str:
        assert operation == "get_object"
        assert Params["Bucket"] == "analytics"
        assert Params["ResponseContentDisposition"].startswith("attachment;")
        return f"https://objects.example/{Params['Key']}?expires={ExpiresIn}"

    def delete_object(self, *, Bucket: str, Key: str) -> None:
        identity = (Bucket, Key)
        self.objects.pop(identity, None)
        self.metadata.pop(identity, None)
        self.modified.pop(identity, None)

    def list_objects_v2(self, **parameters: Any) -> dict[str, Any]:
        bucket = str(parameters["Bucket"])
        prefix = str(parameters["Prefix"])
        after = str(parameters.get("StartAfter", ""))
        limit = int(parameters["MaxKeys"])
        keys = sorted(
            key
            for stored_bucket, key in self.objects
            if stored_bucket == bucket and key.startswith(prefix) and key > after
        )
        selected = keys[:limit]
        return {
            "Contents": [
                {"Key": key, "LastModified": self.modified[(bucket, key)]} for key in selected
            ],
            "IsTruncated": len(keys) > limit,
        }


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def test_local_storage_publishes_atomically_and_rejects_unsafe_keys(tmp_path: Path) -> None:
    storage = LocalObjectStorage(tmp_path)
    candidate = tmp_path / "candidate.part"
    candidate.write_bytes(b"parquet")
    checksum = _digest(b"parquet")

    storage.publish_candidate(
        candidate,
        "datasets/one.parquet",
        byte_size=7,
        checksum_sha256=checksum,
        content_type="application/vnd.apache.parquet",
    )

    assert not candidate.exists()
    with storage.materialize(
        "datasets/one.parquet",
        expected_byte_size=7,
        expected_checksum_sha256=checksum,
    ) as path:
        assert path.read_bytes() == b"parquet"
    with pytest.raises(ObjectStorageUnavailableError, match="size"):
        storage.materialize_path("datasets/one.parquet", expected_byte_size=99)
    with pytest.raises(ValueError):
        storage.materialize_path("../secret")


def test_local_storage_lists_bounded_pages_and_deletes_idempotently(tmp_path: Path) -> None:
    storage = LocalObjectStorage(tmp_path)
    for key in ("datasets/b.parquet", "datasets/a.parquet", "exports/c.csv"):
        path = tmp_path / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(key.encode())

    first = storage.list_page("datasets/", after_key=None, limit=1)
    assert [item.object_key for item in first.objects] == ["datasets/a.parquet"]
    assert first.next_after_key == "datasets/a.parquet"
    second = storage.list_page("datasets/", after_key=first.next_after_key, limit=2)
    assert [item.object_key for item in second.objects] == ["datasets/b.parquet"]
    assert second.next_after_key is None

    storage.delete("datasets/a.parquet")
    storage.delete("datasets/a.parquet")
    assert not (tmp_path / "datasets/a.parquet").exists()
    with pytest.raises(ValueError):
        storage.list_page("datasets", after_key=None, limit=1)
    with pytest.raises(ValueError):
        storage.list_page("datasets/", after_key=None, limit=0)


def test_s3_storage_verifies_upload_download_and_presigns(tmp_path: Path) -> None:
    client = FakeS3Client()
    storage = S3ObjectStorage(
        client=client,
        bucket="analytics",
        work_root=tmp_path,
        server_side_encryption="AES256",
    )
    candidate = tmp_path / "candidate.part"
    candidate.write_bytes(b"snapshot")
    checksum = _digest(b"snapshot")

    storage.publish_candidate(
        candidate,
        "datasets/snapshot.parquet",
        byte_size=8,
        checksum_sha256=checksum,
        content_type="application/vnd.apache.parquet",
    )

    assert not candidate.exists()
    with storage.materialize(
        "datasets/snapshot.parquet",
        expected_byte_size=8,
        expected_checksum_sha256=checksum,
    ) as path:
        assert path.read_bytes() == b"snapshot"
        materialized = path
    assert not materialized.exists()
    url = storage.presign_download(
        "datasets/snapshot.parquet",
        expires_seconds=300,
        filename='report".csv',
        content_type="text/csv",
        expected_byte_size=8,
        expected_checksum_sha256=checksum,
    )
    assert url == "https://objects.example/datasets/snapshot.parquet?expires=300"

    with pytest.raises(ObjectStorageUnavailableError, match="size"):
        storage.presign_download(
            "datasets/snapshot.parquet",
            expires_seconds=300,
            filename="report.csv",
            content_type="text/csv",
            expected_byte_size=99,
            expected_checksum_sha256=checksum,
        )
    client.metadata[("analytics", "datasets/snapshot.parquet")] = {}
    with pytest.raises(ObjectStorageUnavailableError, match="checksum"):
        storage.presign_download(
            "datasets/snapshot.parquet",
            expires_seconds=300,
            filename="report.csv",
            content_type="text/csv",
            expected_byte_size=8,
            expected_checksum_sha256=checksum,
        )
    client.metadata[("analytics", "datasets/snapshot.parquet")] = {"datepipette-sha256": checksum}

    page = storage.list_page("datasets/", after_key=None, limit=1)
    assert [item.object_key for item in page.objects] == ["datasets/snapshot.parquet"]
    storage.delete("datasets/missing.parquet")

    client.objects[("analytics", "datasets/snapshot.parquet")] = b"tampered"
    with (
        pytest.raises(ObjectStorageUnavailableError, match="checksum"),
        storage.materialize(
            "datasets/snapshot.parquet",
            expected_byte_size=8,
            expected_checksum_sha256=checksum,
        ),
    ):
        pass


def test_download_resolver_dispatches_by_durable_backend(tmp_path: Path) -> None:
    client = FakeS3Client()
    s3 = S3ObjectStorage(
        client=client,
        bucket="analytics",
        work_root=tmp_path,
        server_side_encryption=None,
    )
    local = LocalObjectStorage(tmp_path / "local")
    resolver = ObjectExportDownloadResolver(ObjectStorageRegistry("s3", {"local": local, "s3": s3}))
    client.objects[("analytics", "exports/report.csv")] = b"0123456789"
    client.metadata[("analytics", "exports/report.csv")] = {"datepipette-sha256": "0" * 64}

    download = resolver.resolve(
        "s3",
        "exports/report.csv",
        expires_seconds=60,
        filename="report.csv",
        content_type="text/csv",
        expected_byte_size=10,
        expected_checksum_sha256="0" * 64,
    )

    assert download.path is None
    assert download.redirect_url == "https://objects.example/exports/report.csv?expires=60"
    with pytest.raises(ValueError):
        resolver.resolve(
            "unknown",
            "exports/report.csv",
            expires_seconds=60,
            filename="report.csv",
            content_type="text/csv",
            expected_byte_size=None,
            expected_checksum_sha256=None,
        )


def test_download_resolver_proxies_private_s3_and_marks_temporary_file(
    tmp_path: Path,
) -> None:
    client = FakeS3Client()
    payload = b"private export"
    checksum = _digest(payload)
    client.objects[("analytics", "exports/report.csv")] = payload
    client.metadata[("analytics", "exports/report.csv")] = {"datepipette-sha256": checksum}
    storage = S3ObjectStorage(
        client=client,
        bucket="analytics",
        work_root=tmp_path,
        server_side_encryption=None,
    )
    resolver = ObjectExportDownloadResolver(
        ObjectStorageRegistry("s3", {"s3": storage}),
        s3_download_delivery="proxy",
    )

    download = resolver.resolve(
        "s3",
        "exports/report.csv",
        expires_seconds=60,
        filename="report.csv",
        content_type="text/csv",
        expected_byte_size=len(payload),
        expected_checksum_sha256=checksum,
    )

    assert download.redirect_url is None
    assert download.remove_after_send is True
    assert download.path is not None
    temporary_file = Path(download.path)
    assert temporary_file.read_bytes() == payload
    temporary_file.unlink()


def test_download_resolver_rejects_unknown_s3_delivery_mode(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="redirect or proxy"):
        ObjectExportDownloadResolver(
            ObjectStorageRegistry("local", {"local": LocalObjectStorage(tmp_path)}),
            s3_download_delivery="unsupported",
        )


def test_s3_client_requires_explicit_credentials() -> None:
    from datepipette.infrastructure.object_storage import create_s3_client

    with pytest.raises(ValueError, match="credentials"):
        create_s3_client(Settings(environment="test"))


@pytest.mark.skipif(
    "DATEPIPETTE_TEST_S3_ENDPOINT" not in os.environ,
    reason="MinIO integration endpoint is not configured",
)
def test_minio_round_trip_with_runtime_factory(tmp_path: Path) -> None:
    settings = Settings(
        environment="test",
        object_storage_backend="s3",
        snapshot_storage_path=tmp_path,
        s3_endpoint_url=os.environ["DATEPIPETTE_TEST_S3_ENDPOINT"],
        s3_bucket=os.environ.get("DATEPIPETTE_TEST_S3_BUCKET", "datepipette"),
        s3_access_key_id=os.environ["DATEPIPETTE_TEST_S3_ACCESS_KEY_ID"],
        s3_secret_access_key=os.environ["DATEPIPETTE_TEST_S3_SECRET_ACCESS_KEY"],
    )
    storage = create_object_storage_registry(settings).default
    payload = b"real-minio-object"
    candidate = tmp_path / "minio.part"
    candidate.write_bytes(payload)
    key = f"exports/integration/{uuid4()}.bin"

    storage.publish_candidate(
        candidate,
        key,
        byte_size=len(payload),
        checksum_sha256=_digest(payload),
        content_type="application/octet-stream",
    )

    with storage.materialize(
        key,
        expected_byte_size=len(payload),
        expected_checksum_sha256=_digest(payload),
    ) as path:
        assert path.read_bytes() == payload
    download_url = storage.presign_download(
        key,
        expires_seconds=60,
        filename="object.bin",
        content_type="application/octet-stream",
        expected_byte_size=len(payload),
        expected_checksum_sha256=_digest(payload),
    )
    assert download_url is not None
    assert download_url.startswith(settings.s3_endpoint_url or "")
    page = storage.list_page("exports/", after_key=None, limit=1000)
    assert key in {item.object_key for item in page.objects}
    storage.delete(key)
    storage.delete(key)
    with (
        pytest.raises(ObjectStorageUnavailableError),
        storage.materialize(
            key,
            expected_byte_size=len(payload),
            expected_checksum_sha256=_digest(payload),
        ),
    ):
        pass


@pytest.mark.skipif(
    "DATEPIPETTE_TEST_S3_ENDPOINT" not in os.environ,
    reason="MinIO integration endpoint is not configured",
)
def test_minio_snapshot_query_export_and_private_download(tmp_path: Path) -> None:
    settings = Settings(
        environment="test",
        object_storage_backend="s3",
        snapshot_storage_path=tmp_path,
        s3_endpoint_url=os.environ["DATEPIPETTE_TEST_S3_ENDPOINT"],
        s3_bucket=os.environ.get("DATEPIPETTE_TEST_S3_BUCKET", "datepipette"),
        s3_access_key_id=os.environ["DATEPIPETTE_TEST_S3_ACCESS_KEY_ID"],
        s3_secret_access_key=os.environ["DATEPIPETTE_TEST_S3_SECRET_ACCESS_KEY"],
    )
    storages = create_object_storage_registry(settings)
    field_id = uuid4()
    dataset_id = uuid4()
    snapshot_id = uuid4()
    storage_name = f"field_{field_id.hex}"
    snapshot_candidate = tmp_path / "snapshot.parquet.part"
    pq.write_table(
        pa.table({storage_name: pa.array([1, 2, 3], type=pa.int64())}), snapshot_candidate
    )
    snapshot_bytes = snapshot_candidate.stat().st_size
    snapshot_checksum = _digest(snapshot_candidate.read_bytes())
    snapshot_key = f"integration/{snapshot_id}.parquet"
    storages.default.publish_candidate(
        snapshot_candidate,
        snapshot_key,
        byte_size=snapshot_bytes,
        checksum_sha256=snapshot_checksum,
        content_type="application/vnd.apache.parquet",
    )
    field = SnapshotQueryField(
        field_id,
        "record_id",
        storage_name,
        "bigint",
        False,
        True,
        "Record ID",
        None,
    )
    context = ActiveSnapshotQueryContext(
        dataset_id,
        "Orders",
        snapshot_id,
        "s3",
        snapshot_key,
        datetime.now(UTC),
        3,
        (field,),
        SnapshotRefreshState.IDLE,
        None,
        False,
        byte_size=snapshot_bytes,
        checksum_sha256=snapshot_checksum,
    )
    query_executor = DuckDbSnapshotQueryExecutor(
        storages,
        tmp_path,
        limiter=DuckDbQueryConcurrencyLimiter(),
    )
    page = asyncio.run(
        query_executor.query(
            context=context,
            selected_fields=(field,),
            filters=(),
            sorts=(),
            position=None,
            page_size=10,
            runtime=InteractiveQueryRuntime(5, 64, 1, 1, 64, 65_536),
        )
    )
    assert page.rows == ({"record_id": 1}, {"record_id": 2}, {"record_id": 3})

    now = datetime.now(UTC)
    job = ExportJob(
        uuid4(),
        dataset_id,
        snapshot_id,
        uuid4(),
        ExportFormat.CSV,
        ExportState.RUNNING,
        SnapshotQuery((field_id,), (), (), 1, None),
        1,
        now,
        1,
        None,
        None,
        None,
        None,
        None,
        None,
        now,
        now,
        None,
        now + timedelta(hours=24),
        now,
    )
    export = asyncio.run(
        DatasetExportExecutor(
            storages,
            tmp_path,
            memory_limit_mb=64,
            threads=1,
            temporary_directory_limit_mb=64,
            maximum_rows=100,
            fetch_rows=10,
        ).execute(job, context)
    )
    assert export.storage_backend == "s3"
    download = ObjectExportDownloadResolver(storages).resolve(
        export.storage_backend,
        export.object_key,
        expires_seconds=60,
        filename="report.csv",
        content_type=export.content_type,
        expected_byte_size=export.byte_size,
        expected_checksum_sha256=export.checksum_sha256,
    )
    assert download.redirect_url is not None
    with urllib.request.urlopen(download.redirect_url) as response:
        assert response.read().decode("utf-8-sig") == "record_id\n1\n2\n3\n"
    public_url = f"{settings.s3_endpoint_url}/{settings.s3_bucket}/{export.object_key}"
    with pytest.raises(urllib.error.HTTPError) as denied:
        urllib.request.urlopen(public_url)
    assert denied.value.code == 403
