"""Operator backup tooling for private objects and connection secrets."""

import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError  # type: ignore[import-untyped]

from datapipette import backup
from datapipette.backup import (
    OBJECT_MANIFEST,
    BackupIntegrityError,
    KeyVerificationResult,
    export_objects,
    import_objects,
    key_fingerprint,
    read_object_manifest,
    verify_objects,
)
from datapipette.config import Settings
from datapipette.infrastructure.crypto import InvalidEncryptionKeyError

KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
OTHER_KEY = "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE"


def _missing(operation: str) -> ClientError:
    return ClientError({"Error": {"Code": "404", "Message": "hidden"}}, operation)


class FakeS3Client:
    def __init__(self, *, page_size: int = 2) -> None:
        self.objects: dict[str, bytes] = {}
        self.metadata: dict[str, dict[str, str]] = {}
        self.content_types: dict[str, str] = {}
        self.page_size = page_size
        self.vanish_on_download: set[str] = set()
        self.denied: set[str] = set()

    def put(self, key: str, body: bytes, *, checksum: bool = True) -> None:
        self.objects[key] = body
        self.metadata[key] = (
            {"datapipette-sha256": hashlib.sha256(body).hexdigest()} if checksum else {}
        )
        self.content_types[key] = "application/vnd.apache.parquet"

    def list_objects_v2(self, **parameters: Any) -> dict[str, Any]:
        assert parameters["Bucket"] == "analytics"
        keys = sorted(self.objects)
        start = int(parameters.get("ContinuationToken", "0"))
        page = keys[start : start + self.page_size]
        truncated = start + self.page_size < len(keys)
        response: dict[str, Any] = {
            "Contents": [{"Key": key} for key in page],
            "IsTruncated": truncated,
        }
        if truncated:
            response["NextContinuationToken"] = str(start + self.page_size)
        return response

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        assert Bucket == "analytics"
        if Key not in self.objects:
            raise _missing("HeadObject")
        return {
            "ContentLength": len(self.objects[Key]),
            "ContentType": self.content_types.get(Key),
            "Metadata": dict(self.metadata[Key]),
        }

    def download_file(self, bucket: str, key: str, filename: str) -> None:
        assert bucket == "analytics"
        if key in self.denied:
            raise ClientError({"Error": {"Code": "AccessDenied", "Message": "hidden"}}, "Get")
        if key in self.vanish_on_download:
            del self.objects[key]
        if key not in self.objects:
            raise _missing("GetObject")
        Path(filename).write_bytes(self.objects[key])

    def upload_file(
        self,
        filename: str,
        bucket: str,
        key: str,
        *,
        ExtraArgs: dict[str, Any],
    ) -> None:
        assert bucket == "analytics"
        self.objects[key] = Path(filename).read_bytes()
        self.metadata[key] = dict(ExtraArgs["Metadata"])
        if "ContentType" in ExtraArgs:
            self.content_types[key] = ExtraArgs["ContentType"]

    def delete_object(self, *, Bucket: str, Key: str) -> None:
        assert Bucket == "analytics"
        self.objects.pop(Key, None)
        self.metadata.pop(Key, None)


def _populated_client() -> FakeS3Client:
    client = FakeS3Client()
    client.put("snapshots/a/one.parquet", b"first snapshot")
    client.put("snapshots/b/two.parquet", b"second snapshot")
    client.put("exports/c/report.csv", b"id,name\n1,x\n", checksum=False)
    return client


def test_key_fingerprint_is_stable_distinct_and_validates_the_key() -> None:
    fingerprint = key_fingerprint(KEY)
    assert fingerprint == key_fingerprint(KEY + "=")
    assert len(fingerprint) == 64
    assert fingerprint != key_fingerprint(OTHER_KEY)
    assert fingerprint != hashlib.sha256(bytes(32)).hexdigest()
    with pytest.raises(InvalidEncryptionKeyError):
        key_fingerprint("short")


def test_object_backup_round_trip_restores_contents_metadata_and_prunes(tmp_path: Path) -> None:
    source = _populated_client()
    exported = export_objects(source, "analytics", tmp_path / "objects")
    assert (exported.exported, exported.skipped) == (3, 0)
    assert exported.byte_size == sum(len(body) for body in source.objects.values())
    manifest = read_object_manifest(tmp_path / "objects")
    assert [entry.key for entry in manifest] == sorted(source.objects)
    assert not list((tmp_path / "objects").rglob("*.part"))

    target = FakeS3Client(page_size=1)
    target.put("snapshots/stale/orphan.parquet", b"not in backup")
    imported = import_objects(target, "analytics", tmp_path / "objects", prune=True)
    assert (imported.imported, imported.pruned) == (3, 1)
    assert target.objects == source.objects
    for key, body in source.objects.items():
        assert target.metadata[key] == {"datapipette-sha256": hashlib.sha256(body).hexdigest()}
        assert target.content_types[key] == "application/vnd.apache.parquet"

    kept = FakeS3Client()
    kept.put("snapshots/extra.parquet", b"kept")
    assert import_objects(kept, "analytics", tmp_path / "objects", prune=False).pruned == 0
    assert "snapshots/extra.parquet" in kept.objects


def test_export_skips_objects_removed_by_concurrent_cleanup(tmp_path: Path) -> None:
    client = _populated_client()
    client.vanish_on_download.add("snapshots/a/one.parquet")
    exported = export_objects(client, "analytics", tmp_path / "objects")
    assert (exported.exported, exported.skipped) == (2, 1)
    assert "snapshots/a/one.parquet" not in {
        entry.key for entry in read_object_manifest(tmp_path / "objects")
    }


def test_export_propagates_storage_errors_and_rejects_corrupt_objects(tmp_path: Path) -> None:
    denied = _populated_client()
    denied.denied.add("snapshots/a/one.parquet")
    with pytest.raises(ClientError):
        export_objects(denied, "analytics", tmp_path / "denied")
    assert not list((tmp_path / "denied").rglob("*.part"))

    corrupt = _populated_client()
    corrupt.metadata["snapshots/a/one.parquet"]["datapipette-sha256"] = "0" * 64
    with pytest.raises(BackupIntegrityError, match=r"snapshots/a/one\.parquet"):
        export_objects(corrupt, "analytics", tmp_path / "corrupt")


def test_export_requires_an_empty_directory_and_safe_keys(tmp_path: Path) -> None:
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "file").write_text("x")
    with pytest.raises(BackupIntegrityError, match="empty"):
        export_objects(_populated_client(), "analytics", occupied)

    unsafe = FakeS3Client()
    unsafe.put("../escape.parquet", b"x")
    with pytest.raises(BackupIntegrityError, match="normalized"):
        export_objects(unsafe, "analytics", tmp_path / "unsafe")


def test_verification_detects_tampering_and_missing_files(tmp_path: Path) -> None:
    root = tmp_path / "objects"
    export_objects(_populated_client(), "analytics", root)
    assert len(verify_objects(root)) == 3

    target = root / "data" / "snapshots" / "a" / "one.parquet"
    target.write_bytes(b"tampered snapshot")
    with pytest.raises(BackupIntegrityError, match=r"snapshots/a/one\.parquet"):
        verify_objects(root)
    with pytest.raises(BackupIntegrityError):
        import_objects(FakeS3Client(), "analytics", root, prune=True)

    target.unlink()
    with pytest.raises(BackupIntegrityError, match=r"snapshots/a/one\.parquet"):
        verify_objects(root)


def test_import_rejects_a_size_mismatch_after_upload(tmp_path: Path) -> None:
    root = tmp_path / "objects"
    export_objects(_populated_client(), "analytics", root)

    class TruncatingClient(FakeS3Client):
        def upload_file(
            self,
            filename: str,
            bucket: str,
            key: str,
            *,
            ExtraArgs: dict[str, Any],
        ) -> None:
            super().upload_file(filename, bucket, key, ExtraArgs=ExtraArgs)
            self.objects[key] = self.objects[key][:-1]

    with pytest.raises(BackupIntegrityError, match="Restored object"):
        import_objects(TruncatingClient(), "analytics", root, prune=False)


@pytest.mark.parametrize(
    "lines",
    [
        ["not json"],
        [json.dumps({"key": "a"})],
        [json.dumps({"key": "/abs", "byte_size": 1, "sha256": "x"})],
        [json.dumps({"key": "a", "byte_size": 1, "sha256": "x"})] * 2,
    ],
)
def test_manifest_rejects_malformed_entries(tmp_path: Path, lines: list[str]) -> None:
    (tmp_path / OBJECT_MANIFEST).write_text("\n".join(lines) + "\n\n", encoding="utf-8")
    with pytest.raises(BackupIntegrityError):
        read_object_manifest(tmp_path)


def test_manifest_must_exist(tmp_path: Path) -> None:
    with pytest.raises(BackupIntegrityError, match="missing"):
        read_object_manifest(tmp_path)


def _settings(**values: Any) -> Settings:
    defaults: dict[str, Any] = {
        "environment": "test",
        "object_storage_backend": "s3",
        "s3_bucket": "analytics",
        "s3_access_key_id": "access",
        "s3_secret_access_key": "secret",
        "connection_encryption_key": KEY,
    }
    defaults.update(values)
    return Settings(**defaults)


def test_cli_runs_fingerprint_and_object_commands(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = _populated_client()
    target = FakeS3Client()
    clients = iter([source, target])
    monkeypatch.setattr(backup, "get_settings", _settings)
    monkeypatch.setattr(backup, "create_s3_client", lambda _settings: next(clients))
    root = tmp_path / "objects"

    backup.main(["key-fingerprint"])
    backup.main(["objects-export", "--dir", str(root)])
    backup.main(["objects-verify", "--dir", str(root)])
    backup.main(["objects-import", "--dir", str(root), "--prune"])

    output = capsys.readouterr().out.splitlines()
    assert output[0] == key_fingerprint(KEY)
    assert output[1].startswith("Exported 3 objects")
    assert output[2] == "Verified 3 objects"
    assert output[3].startswith("Imported 3 objects")
    assert KEY not in "\n".join(output)
    assert target.objects == source.objects

    with pytest.raises(SystemExit, match="manifest is missing"):
        backup.main(["objects-verify", "--dir", str(tmp_path / "absent")])


def test_cli_requires_s3_storage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(backup, "get_settings", lambda: _settings(object_storage_backend="local"))
    with pytest.raises(SystemExit, match="S3 object storage"):
        backup.main(["objects-export", "--dir", str(tmp_path)])


def test_cli_reports_key_verification_without_secrets(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    failed_id = uuid4()
    results = iter(
        [
            KeyVerificationResult(connections=2, secrets=3, failed_connection_ids=()),
            KeyVerificationResult(connections=2, secrets=1, failed_connection_ids=(failed_id,)),
        ]
    )

    async def fake_verify(_settings: Settings) -> KeyVerificationResult:
        return next(results)

    monkeypatch.setattr(backup, "get_settings", _settings)
    monkeypatch.setattr(backup, "verify_connection_secrets", fake_verify)

    backup.main(["verify-key"])
    assert capsys.readouterr().out.strip() == "Decrypted 3 secrets across 2 connections"

    with pytest.raises(SystemExit) as raised:
        backup.main(["verify-key"])
    assert raised.value.code == 1
    assert str(failed_id) in capsys.readouterr().err
