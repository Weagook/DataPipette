"""Operator backup commands for private object storage and metadata secrets."""

import argparse
import asyncio
import hashlib
import json
import os
import sys
from collections.abc import Iterator, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import UUID

from botocore.exceptions import ClientError  # type: ignore[import-untyped]
from sqlalchemy import select

from datapipette.application.connections import ConnectionSecretUnavailableError
from datapipette.config import Settings, get_settings
from datapipette.infrastructure.crypto import (
    AesGcmSecretCipher,
    InvalidEncryptionKeyError,
    decode_encryption_key,
)
from datapipette.infrastructure.database import create_database_engine, create_session_factory
from datapipette.infrastructure.models import PostgresConnectionModel
from datapipette.infrastructure.object_storage import create_s3_client

OBJECT_MANIFEST = "objects.jsonl"
OBJECT_DATA_DIRECTORY = "data"
_CHECKSUM_METADATA = "datapipette-sha256"
_FINGERPRINT_CONTEXT = b"datapipette:connection-encryption-key:fingerprint:v1:"
_MISSING_OBJECT_CODES = {"404", "NoSuchKey", "NotFound"}


class BackupIntegrityError(RuntimeError):
    """A backup set or a stored object failed verification."""


@dataclass(frozen=True, slots=True)
class BackupObject:
    key: str
    byte_size: int
    sha256: str
    content_type: str | None


@dataclass(frozen=True, slots=True)
class ObjectExportResult:
    exported: int
    skipped: int
    byte_size: int


@dataclass(frozen=True, slots=True)
class ObjectImportResult:
    imported: int
    pruned: int
    byte_size: int


class KeyRotationError(RuntimeError):
    """Stored secrets could not be re-encrypted; nothing was changed."""


NEW_KEY_ENVIRONMENT = "DATAPIPETTE_NEW_CONNECTION_ENCRYPTION_KEY"


@dataclass(frozen=True, slots=True)
class KeyRotationResult:
    connections: int
    secrets: int


@dataclass(frozen=True, slots=True)
class KeyVerificationResult:
    connections: int
    secrets: int
    failed_connection_ids: tuple[UUID, ...]


def key_fingerprint(encoded_key: str) -> str:
    """Identify an encryption key without storing material that can decrypt anything."""
    return hashlib.sha256(_FINGERPRINT_CONTEXT + decode_encryption_key(encoded_key)).hexdigest()


def _object_key(value: str) -> str:
    parsed = PurePosixPath(value)
    if not value or parsed.is_absolute() or ".." in parsed.parts or str(parsed) != value:
        raise BackupIntegrityError("Object key is not a normalized relative path")
    return value


def _checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _list_keys(client: Any, bucket: str) -> Iterator[str]:
    parameters: dict[str, Any] = {"Bucket": bucket}
    while True:
        response = client.list_objects_v2(**parameters)
        for item in response.get("Contents", []):
            yield str(item["Key"])
        token = response.get("NextContinuationToken")
        if not response.get("IsTruncated") or not token:
            return
        parameters["ContinuationToken"] = token


def _is_missing(error: ClientError) -> bool:
    return str(error.response.get("Error", {}).get("Code")) in _MISSING_OBJECT_CODES


def _data_path(root: Path, key: str) -> Path:
    data_root = (root / OBJECT_DATA_DIRECTORY).resolve()
    path = (data_root / _object_key(key)).resolve()
    if not path.is_relative_to(data_root):
        raise BackupIntegrityError("Object key escapes the backup directory")
    return path


def export_objects(client: Any, bucket: str, target: Path) -> ObjectExportResult:
    """Copy every bucket object into an empty directory with a checksum manifest."""
    if target.exists() and any(target.iterdir()):
        raise BackupIntegrityError("Object export directory must be empty")
    target.mkdir(parents=True, exist_ok=True)
    entries: list[BackupObject] = []
    skipped = 0
    for key in sorted(_list_keys(client, bucket)):
        destination = _data_path(target, key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        candidate = destination.with_name(f"{destination.name}.part")
        try:
            head = client.head_object(Bucket=bucket, Key=key)
            client.download_file(bucket, key, str(candidate))
        except ClientError as exc:
            candidate.unlink(missing_ok=True)
            if _is_missing(exc):
                # Lifecycle cleanup may remove superseded objects during the copy.
                skipped += 1
                continue
            raise
        checksum = _checksum(candidate)
        byte_size = candidate.stat().st_size
        metadata = head.get("Metadata") or {}
        recorded = metadata.get(_CHECKSUM_METADATA) if isinstance(metadata, dict) else None
        if byte_size != int(head.get("ContentLength", -1)) or (
            recorded is not None and recorded != checksum
        ):
            candidate.unlink(missing_ok=True)
            raise BackupIntegrityError(f"Stored object failed verification: {key}")
        os.replace(candidate, destination)
        content_type = head.get("ContentType")
        entries.append(
            BackupObject(
                key=key,
                byte_size=byte_size,
                sha256=checksum,
                content_type=str(content_type) if content_type else None,
            )
        )
    with (target / OBJECT_MANIFEST).open("w", encoding="utf-8") as stream:
        for entry in entries:
            stream.write(json.dumps(asdict(entry), sort_keys=True) + "\n")
    return ObjectExportResult(
        exported=len(entries),
        skipped=skipped,
        byte_size=sum(entry.byte_size for entry in entries),
    )


def read_object_manifest(source: Path) -> tuple[BackupObject, ...]:
    manifest = source / OBJECT_MANIFEST
    if not manifest.is_file():
        raise BackupIntegrityError("Object manifest is missing")
    entries: list[BackupObject] = []
    seen: set[str] = set()
    with manifest.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
                entry = BackupObject(
                    key=_object_key(str(raw["key"])),
                    byte_size=int(raw["byte_size"]),
                    sha256=str(raw["sha256"]),
                    content_type=raw.get("content_type"),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise BackupIntegrityError("Object manifest is malformed") from exc
            if entry.key in seen:
                raise BackupIntegrityError("Object manifest contains a duplicate key")
            seen.add(entry.key)
            entries.append(entry)
    return tuple(entries)


def verify_objects(source: Path) -> tuple[BackupObject, ...]:
    """Check every backed-up object against its recorded size and SHA-256."""
    entries = read_object_manifest(source)
    for entry in entries:
        path = _data_path(source, entry.key)
        if (
            not path.is_file()
            or path.stat().st_size != entry.byte_size
            or _checksum(path) != entry.sha256
        ):
            raise BackupIntegrityError(f"Backed-up object failed verification: {entry.key}")
    return entries


def import_objects(
    client: Any,
    bucket: str,
    source: Path,
    *,
    prune: bool,
) -> ObjectImportResult:
    """Restore verified objects with their checksum metadata, optionally removing extras."""
    entries = verify_objects(source)
    for entry in entries:
        extra: dict[str, Any] = {"Metadata": {_CHECKSUM_METADATA: entry.sha256}}
        if entry.content_type is not None:
            extra["ContentType"] = entry.content_type
        client.upload_file(str(_data_path(source, entry.key)), bucket, entry.key, ExtraArgs=extra)
        head = client.head_object(Bucket=bucket, Key=entry.key)
        if int(head.get("ContentLength", -1)) != entry.byte_size:
            raise BackupIntegrityError(f"Restored object failed verification: {entry.key}")
    pruned = 0
    if prune:
        expected = {entry.key for entry in entries}
        for key in [key for key in _list_keys(client, bucket) if key not in expected]:
            client.delete_object(Bucket=bucket, Key=key)
            pruned += 1
    return ObjectImportResult(
        imported=len(entries),
        pruned=pruned,
        byte_size=sum(entry.byte_size for entry in entries),
    )


async def verify_connection_secrets(settings: Settings) -> KeyVerificationResult:
    """Prove that the configured key decrypts every stored source-connection secret."""
    cipher = AesGcmSecretCipher(settings.connection_encryption_key.get_secret_value())
    engine = create_database_engine(settings)
    try:
        async with create_session_factory(engine)() as session:
            rows = (
                await session.execute(
                    select(
                        PostgresConnectionModel.id,
                        PostgresConnectionModel.encrypted_password,
                        PostgresConnectionModel.encrypted_ca_certificate,
                    ).order_by(PostgresConnectionModel.id)
                )
            ).all()
    finally:
        await engine.dispose()
    secrets = 0
    failed: list[UUID] = []
    for connection_id, password, ca_certificate in rows:
        fields = [("password", password)]
        if ca_certificate is not None:
            fields.append(("ca_certificate", ca_certificate))
        try:
            for field, envelope in fields:
                cipher.decrypt(envelope, connection_id=connection_id, field=field)
                secrets += 1
        except ConnectionSecretUnavailableError:
            failed.append(connection_id)
    return KeyVerificationResult(len(rows), secrets, tuple(failed))


async def rotate_connection_secrets(settings: Settings, new_key: str) -> KeyRotationResult:
    """Re-encrypt every stored source-connection secret with a new key in one transaction.

    Every secret is decrypted with the configured key first; if any fails, the
    transaction is rolled back and nothing is changed.
    """
    decode_encryption_key(new_key)
    current_key = settings.connection_encryption_key.get_secret_value()
    if new_key == current_key:
        raise KeyRotationError("The new key is the same as the configured key")
    current = AesGcmSecretCipher(current_key)
    replacement = AesGcmSecretCipher(new_key)
    engine = create_database_engine(settings)
    secrets = 0
    try:
        async with create_session_factory(engine)() as session, session.begin():
            connections = list(
                await session.scalars(
                    select(PostgresConnectionModel)
                    .order_by(PostgresConnectionModel.id)
                    .with_for_update()
                )
            )
            failed: list[UUID] = []
            for connection in connections:
                try:
                    password = current.decrypt(
                        connection.encrypted_password, connection_id=connection.id, field="password"
                    )
                    ca_certificate = (
                        current.decrypt(
                            connection.encrypted_ca_certificate,
                            connection_id=connection.id,
                            field="ca_certificate",
                        )
                        if connection.encrypted_ca_certificate is not None
                        else None
                    )
                except ConnectionSecretUnavailableError:
                    failed.append(connection.id)
                    continue
                connection.encrypted_password = replacement.encrypt(
                    password, connection_id=connection.id, field="password"
                )
                secrets += 1
                if ca_certificate is not None:
                    connection.encrypted_ca_certificate = replacement.encrypt(
                        ca_certificate, connection_id=connection.id, field="ca_certificate"
                    )
                    secrets += 1
            if failed:
                raise KeyRotationError(
                    f"{len(failed)} connections cannot be decrypted with the configured key: "
                    + ", ".join(str(item) for item in failed)
                )
    finally:
        await engine.dispose()
    return KeyRotationResult(len(connections), secrets)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="datapipette-backup")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "key-fingerprint",
        help="Print a non-reversible fingerprint of the connection encryption key",
    )
    for name, help_text in (
        ("objects-export", "Copy the private object bucket into an empty directory"),
        ("objects-verify", "Verify an object backup directory against its manifest"),
        ("objects-import", "Restore an object backup directory into the private bucket"),
    ):
        command = subparsers.add_parser(name, help=help_text)
        command.add_argument("--dir", type=Path, required=True)
        if name == "objects-import":
            command.add_argument(
                "--prune",
                action="store_true",
                help="Delete bucket objects that are absent from the backup",
            )
    subparsers.add_parser(
        "verify-key",
        help="Check that every stored connection secret decrypts with the configured key",
    )
    subparsers.add_parser(
        "rotate-key",
        help=f"Re-encrypt every stored connection secret with the key in {NEW_KEY_ENVIRONMENT}",
    )
    return parser


def _bucket(settings: Settings) -> str:
    if settings.object_storage_backend != "s3" or settings.s3_bucket is None:
        raise SystemExit("S3 object storage must be configured")
    return settings.s3_bucket


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    settings = get_settings()
    try:
        if args.command == "key-fingerprint":
            print(key_fingerprint(settings.connection_encryption_key.get_secret_value()))
        elif args.command == "objects-export":
            exported = export_objects(create_s3_client(settings), _bucket(settings), args.dir)
            print(
                f"Exported {exported.exported} objects ({exported.byte_size} bytes); "
                f"skipped {exported.skipped} removed during export"
            )
        elif args.command == "objects-verify":
            entries = verify_objects(args.dir)
            print(f"Verified {len(entries)} objects")
        elif args.command == "objects-import":
            imported = import_objects(
                create_s3_client(settings),
                _bucket(settings),
                args.dir,
                prune=args.prune,
            )
            print(
                f"Imported {imported.imported} objects ({imported.byte_size} bytes); "
                f"pruned {imported.pruned}"
            )
        elif args.command == "rotate-key":
            new_key = os.environ.get(NEW_KEY_ENVIRONMENT, "")
            if not new_key:
                raise SystemExit(f"{NEW_KEY_ENVIRONMENT} must be set to the new key")
            rotated = asyncio.run(rotate_connection_secrets(settings, new_key))
            print(
                f"Re-encrypted {rotated.secrets} secrets across {rotated.connections} connections"
            )
            print(f"New key fingerprint: {key_fingerprint(new_key)}")
        else:
            result = asyncio.run(verify_connection_secrets(settings))
            if result.failed_connection_ids:
                failed = ", ".join(str(item) for item in result.failed_connection_ids)
                print(
                    f"{len(result.failed_connection_ids)} of {result.connections} connections "
                    f"cannot be decrypted with the configured key: {failed}",
                    file=sys.stderr,
                )
                raise SystemExit(1)
            print(f"Decrypted {result.secrets} secrets across {result.connections} connections")
    except (BackupIntegrityError, KeyRotationError, InvalidEncryptionKeyError) as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
