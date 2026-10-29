"""Private local and S3-compatible analytical object storage."""

import hashlib
import os
import re
import threading
import time
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from heapq import nsmallest
from pathlib import Path, PurePosixPath
from typing import Any, Protocol
from uuid import uuid4

import boto3  # type: ignore[import-untyped]
from boto3.s3.transfer import TransferConfig  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]

from datapipette.application.storage_cleanup import StoredObject, StoredObjectPage
from datapipette.config import Settings
from datapipette.domain.exports import ExportDownload

_TRANSFER_CHUNK_BYTES = 8 * 1024 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ObjectStorageUnavailableError(OSError):
    """The configured private object could not be stored or read safely."""


class ObjectStorage(Protocol):
    backend: str

    def publish_candidate(
        self,
        candidate: Path,
        object_key: str,
        *,
        byte_size: int,
        checksum_sha256: str,
        content_type: str,
    ) -> None: ...

    def materialize(
        self,
        object_key: str,
        *,
        expected_byte_size: int | None,
        expected_checksum_sha256: str | None,
    ) -> AbstractContextManager[Path]: ...

    def presign_download(
        self,
        object_key: str,
        *,
        expires_seconds: int,
        filename: str,
        content_type: str,
        expected_byte_size: int | None,
        expected_checksum_sha256: str | None,
    ) -> str | None: ...

    def prepare_download(
        self,
        object_key: str,
        *,
        expected_byte_size: int | None,
        expected_checksum_sha256: str | None,
    ) -> tuple[Path, bool]: ...

    def delete(self, object_key: str) -> None: ...

    def list_page(
        self,
        prefix: str,
        *,
        after_key: str | None,
        limit: int,
    ) -> "StoredObjectPage": ...


def _key(value: str) -> str:
    parsed = PurePosixPath(value)
    if not value or parsed.is_absolute() or ".." in parsed.parts or str(parsed) != value:
        raise ValueError("Object key must be a normalized relative POSIX path")
    return value


def _prefix(value: str) -> str:
    if not value.endswith("/"):
        raise ValueError("Object prefix must end with a slash")
    _key(value[:-1])
    return value


def _checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _verify(
    path: Path,
    *,
    expected_byte_size: int | None,
    expected_checksum_sha256: str | None,
) -> None:
    if not path.is_file():
        raise ObjectStorageUnavailableError("Object is unavailable")
    if expected_byte_size is not None and path.stat().st_size != expected_byte_size:
        raise ObjectStorageUnavailableError("Object size verification failed")
    if expected_checksum_sha256 is not None and _checksum(path) != expected_checksum_sha256:
        raise ObjectStorageUnavailableError("Object checksum verification failed")


class VerifiedObjectCache:
    """Content-addressed local copies of immutable objects, verified once on insert.

    Snapshot objects never change after publication, so a copy whose SHA-256
    was checked when it entered the cache can serve every later read. Entries
    are evicted least-recently-used once the cache exceeds its byte budget,
    but never while recently used, because a reader may be about to open it.
    """

    def __init__(self, root: Path, *, max_bytes: int, min_idle_seconds: float = 300.0) -> None:
        if max_bytes < 1:
            raise ValueError("Object cache budget must be positive")
        self.root = root.resolve()
        self.max_bytes = max_bytes
        self._min_idle_seconds = min_idle_seconds
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}

    def accepts(self, *, byte_size: int | None, checksum_sha256: str | None) -> bool:
        return (
            byte_size is not None
            and 0 <= byte_size <= self.max_bytes
            and checksum_sha256 is not None
            and _SHA256.fullmatch(checksum_sha256) is not None
        )

    def lock(self, checksum_sha256: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(checksum_sha256, threading.Lock())

    def staging_directory(self) -> Path:
        directory = self.root / ".incoming"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        return directory

    def _path(self, checksum_sha256: str) -> Path:
        return self.root / checksum_sha256[:2] / f"{checksum_sha256}.object"

    def lookup(self, checksum_sha256: str, *, byte_size: int) -> Path | None:
        path = self._path(checksum_sha256)
        try:
            if path.stat().st_size != byte_size:
                path.unlink(missing_ok=True)
                return None
            # The modification time records the last use for eviction order.
            os.utime(path)
        except FileNotFoundError:
            return None
        return path

    def install(self, verified_candidate: Path, checksum_sha256: str) -> Path:
        path = self._path(checksum_sha256)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.replace(verified_candidate, path)
        self._evict(keep=path)
        return path

    def _evict(self, *, keep: Path) -> None:
        entries: list[tuple[float, int, Path]] = []
        for candidate in self.root.glob("??/*.object"):
            try:
                status = candidate.stat()
            except FileNotFoundError:
                continue
            entries.append((status.st_mtime, status.st_size, candidate))
        total = sum(size for _modified, size, _path in entries)
        idle_before = time.time() - self._min_idle_seconds
        for modified, size, candidate in sorted(entries):
            if total <= self.max_bytes:
                return
            if candidate == keep or modified > idle_before:
                continue
            candidate.unlink(missing_ok=True)
            total -= size


class LocalObjectStorage:
    backend = "local"

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _path(self, object_key: str) -> Path:
        path = (self.root / _key(object_key)).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Object key escapes the storage root")
        return path

    def publish_candidate(
        self,
        candidate: Path,
        object_key: str,
        *,
        byte_size: int,
        checksum_sha256: str,
        content_type: str,
    ) -> None:
        del content_type
        _verify(
            candidate,
            expected_byte_size=byte_size,
            expected_checksum_sha256=checksum_sha256,
        )
        target = self._path(object_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(candidate, target)
        descriptor = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @contextmanager
    def materialize(
        self,
        object_key: str,
        *,
        expected_byte_size: int | None,
        expected_checksum_sha256: str | None,
    ) -> Iterator[Path]:
        path = self._path(object_key)
        _verify(
            path,
            expected_byte_size=expected_byte_size,
            expected_checksum_sha256=None,
        )
        yield path

    def presign_download(
        self,
        object_key: str,
        *,
        expires_seconds: int,
        filename: str,
        content_type: str,
        expected_byte_size: int | None,
        expected_checksum_sha256: str | None,
    ) -> str | None:
        del expires_seconds, filename, content_type
        self.materialize_path(
            object_key,
            expected_byte_size=expected_byte_size,
            expected_checksum_sha256=expected_checksum_sha256,
        )
        return None

    def materialize_path(
        self,
        object_key: str,
        *,
        expected_byte_size: int | None = None,
        expected_checksum_sha256: str | None = None,
    ) -> Path:
        path = self._path(object_key)
        _verify(
            path,
            expected_byte_size=expected_byte_size,
            expected_checksum_sha256=expected_checksum_sha256,
        )
        return path

    def prepare_download(
        self,
        object_key: str,
        *,
        expected_byte_size: int | None,
        expected_checksum_sha256: str | None,
    ) -> tuple[Path, bool]:
        return (
            self.materialize_path(
                object_key,
                expected_byte_size=expected_byte_size,
                expected_checksum_sha256=expected_checksum_sha256,
            ),
            False,
        )

    def delete(self, object_key: str) -> None:
        self._path(object_key).unlink(missing_ok=True)

    def list_page(
        self,
        prefix: str,
        *,
        after_key: str | None,
        limit: int,
    ) -> StoredObjectPage:
        normalized_prefix = _prefix(prefix)
        if limit < 1:
            raise ValueError("Object listing limit must be positive")
        if after_key is not None and not _key(after_key).startswith(normalized_prefix):
            raise ValueError("Object listing cursor must remain inside its prefix")
        prefix_root = self._path(normalized_prefix[:-1])

        def candidates() -> Iterator[StoredObject]:
            if not prefix_root.exists():
                return
            for candidate in prefix_root.rglob("*"):
                resolved = candidate.resolve()
                if (
                    not candidate.is_file()
                    or not resolved.is_relative_to(self.root)
                    or candidate.is_symlink()
                ):
                    continue
                object_key = candidate.relative_to(self.root).as_posix()
                if after_key is not None and object_key <= after_key:
                    continue
                yield StoredObject(
                    object_key,
                    datetime.fromtimestamp(candidate.stat().st_mtime, UTC),
                )

        objects = nsmallest(limit + 1, candidates(), key=lambda item: item.object_key)
        page = tuple(objects[:limit])
        next_after_key = page[-1].object_key if len(objects) > limit else None
        return StoredObjectPage(page, next_after_key)


class S3ObjectStorage:
    backend = "s3"

    def __init__(
        self,
        *,
        client: Any,
        bucket: str,
        work_root: Path,
        server_side_encryption: str | None,
        cache: VerifiedObjectCache | None = None,
    ) -> None:
        self._client = client
        self._bucket = bucket
        self._work_root = work_root.resolve()
        self._server_side_encryption = server_side_encryption
        self._cache = cache
        self._transfer = TransferConfig(
            multipart_threshold=_TRANSFER_CHUNK_BYTES,
            multipart_chunksize=_TRANSFER_CHUNK_BYTES,
            max_concurrency=4,
            use_threads=True,
        )

    def publish_candidate(
        self,
        candidate: Path,
        object_key: str,
        *,
        byte_size: int,
        checksum_sha256: str,
        content_type: str,
    ) -> None:
        key = _key(object_key)
        _verify(
            candidate,
            expected_byte_size=byte_size,
            expected_checksum_sha256=checksum_sha256,
        )
        extra: dict[str, Any] = {
            "ContentType": content_type,
            "Metadata": {"datapipette-sha256": checksum_sha256},
        }
        if self._server_side_encryption is not None:
            extra["ServerSideEncryption"] = self._server_side_encryption
        try:
            self._client.upload_file(
                str(candidate),
                self._bucket,
                key,
                ExtraArgs=extra,
                Config=self._transfer,
            )
            head = self._client.head_object(Bucket=self._bucket, Key=key)
        except Exception as exc:
            raise ObjectStorageUnavailableError("S3 upload failed") from exc
        if int(head.get("ContentLength", -1)) != byte_size:
            raise ObjectStorageUnavailableError("S3 object size verification failed")
        metadata = head.get("Metadata")
        if not isinstance(metadata, dict) or metadata.get("datapipette-sha256") != checksum_sha256:
            raise ObjectStorageUnavailableError("S3 object checksum metadata verification failed")
        candidate.unlink(missing_ok=True)

    @contextmanager
    def materialize(
        self,
        object_key: str,
        *,
        expected_byte_size: int | None,
        expected_checksum_sha256: str | None,
    ) -> Iterator[Path]:
        cached = self._cached_copy(
            object_key,
            expected_byte_size=expected_byte_size,
            expected_checksum_sha256=expected_checksum_sha256,
        )
        if cached is not None:
            yield cached
            return
        target = self._download_to_temporary_file(
            object_key,
            expected_byte_size=expected_byte_size,
            expected_checksum_sha256=expected_checksum_sha256,
        )
        try:
            yield target
        finally:
            target.unlink(missing_ok=True)

    def _cached_copy(
        self,
        object_key: str,
        *,
        expected_byte_size: int | None,
        expected_checksum_sha256: str | None,
    ) -> Path | None:
        cache = self._cache
        if cache is None or not cache.accepts(
            byte_size=expected_byte_size,
            checksum_sha256=expected_checksum_sha256,
        ):
            return None
        assert expected_byte_size is not None and expected_checksum_sha256 is not None
        try:
            # One download per object and process; concurrent first readers wait.
            with cache.lock(expected_checksum_sha256):
                cached = cache.lookup(expected_checksum_sha256, byte_size=expected_byte_size)
                if cached is not None:
                    return cached
                downloaded = self._download_to_temporary_file(
                    object_key,
                    expected_byte_size=expected_byte_size,
                    expected_checksum_sha256=expected_checksum_sha256,
                    directory=cache.staging_directory(),
                )
                try:
                    return cache.install(downloaded, expected_checksum_sha256)
                finally:
                    downloaded.unlink(missing_ok=True)
        except ObjectStorageUnavailableError:
            raise
        except OSError:
            # A full or unavailable cache volume must not make snapshots unreadable.
            return None

    def _download_to_temporary_file(
        self,
        object_key: str,
        *,
        expected_byte_size: int | None,
        expected_checksum_sha256: str | None,
        directory: Path | None = None,
    ) -> Path:
        key = _key(object_key)
        if directory is None:
            directory = self._work_root / ".object-tmp"
            directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{uuid4().hex}.object"
        candidate = target.with_suffix(".part")
        try:
            self._client.download_file(
                self._bucket,
                key,
                str(candidate),
                Config=self._transfer,
            )
            _verify(
                candidate,
                expected_byte_size=expected_byte_size,
                expected_checksum_sha256=expected_checksum_sha256,
            )
            os.replace(candidate, target)
            return target
        except ObjectStorageUnavailableError:
            raise
        except Exception as exc:
            raise ObjectStorageUnavailableError("S3 download failed") from exc
        finally:
            candidate.unlink(missing_ok=True)

    def prepare_download(
        self,
        object_key: str,
        *,
        expected_byte_size: int | None,
        expected_checksum_sha256: str | None,
    ) -> tuple[Path, bool]:
        return (
            self._download_to_temporary_file(
                object_key,
                expected_byte_size=expected_byte_size,
                expected_checksum_sha256=expected_checksum_sha256,
            ),
            True,
        )

    def presign_download(
        self,
        object_key: str,
        *,
        expires_seconds: int,
        filename: str,
        content_type: str,
        expected_byte_size: int | None,
        expected_checksum_sha256: str | None,
    ) -> str | None:
        key = _key(object_key)
        safe_filename = filename.replace('"', "").replace("/", "-").replace("\\", "-")
        try:
            head = self._client.head_object(Bucket=self._bucket, Key=key)
            if expected_byte_size is not None and int(head.get("ContentLength", -1)) != (
                expected_byte_size
            ):
                raise ObjectStorageUnavailableError("S3 object size verification failed")
            metadata = head.get("Metadata")
            if expected_checksum_sha256 is not None and (
                not isinstance(metadata, dict)
                or metadata.get("datapipette-sha256") != expected_checksum_sha256
            ):
                raise ObjectStorageUnavailableError(
                    "S3 object checksum metadata verification failed"
                )
            return str(
                self._client.generate_presigned_url(
                    "get_object",
                    Params={
                        "Bucket": self._bucket,
                        "Key": key,
                        "ResponseContentDisposition": (f'attachment; filename="{safe_filename}"'),
                        "ResponseContentType": content_type,
                    },
                    ExpiresIn=expires_seconds,
                )
            )
        except ObjectStorageUnavailableError:
            raise
        except Exception as exc:
            raise ObjectStorageUnavailableError("S3 download signing failed") from exc

    def delete(self, object_key: str) -> None:
        try:
            self._client.delete_object(Bucket=self._bucket, Key=_key(object_key))
        except Exception as exc:
            raise ObjectStorageUnavailableError("S3 object deletion failed") from exc

    def list_page(
        self,
        prefix: str,
        *,
        after_key: str | None,
        limit: int,
    ) -> StoredObjectPage:
        normalized_prefix = _prefix(prefix)
        if limit < 1:
            raise ValueError("Object listing limit must be positive")
        if after_key is not None and not _key(after_key).startswith(normalized_prefix):
            raise ValueError("Object listing cursor must remain inside its prefix")
        parameters: dict[str, Any] = {
            "Bucket": self._bucket,
            "Prefix": normalized_prefix,
            "MaxKeys": limit,
        }
        if after_key is not None:
            parameters["StartAfter"] = _key(after_key)
        try:
            response = self._client.list_objects_v2(**parameters)
            objects = tuple(
                StoredObject(str(item["Key"]), item["LastModified"])
                for item in response.get("Contents", [])
            )
        except Exception as exc:
            raise ObjectStorageUnavailableError("S3 object listing failed") from exc
        next_after_key = objects[-1].object_key if response.get("IsTruncated") and objects else None
        return StoredObjectPage(objects, next_after_key)


@dataclass(frozen=True, slots=True)
class ObjectStorageRegistry:
    default_backend: str
    storages: dict[str, ObjectStorage]

    @property
    def default(self) -> ObjectStorage:
        return self.require(self.default_backend)

    def require(self, backend: str) -> ObjectStorage:
        try:
            return self.storages[backend]
        except KeyError as exc:
            raise ObjectStorageUnavailableError("Object storage backend is unavailable") from exc


class ObjectExportDownloadResolver:
    def __init__(
        self,
        storages: ObjectStorageRegistry,
        *,
        s3_download_delivery: str = "redirect",
    ) -> None:
        if s3_download_delivery not in {"redirect", "proxy"}:
            raise ValueError("S3 download delivery must be redirect or proxy")
        self._storages = storages
        self._s3_download_delivery = s3_download_delivery

    def resolve(
        self,
        storage_backend: str,
        object_key: str,
        *,
        expires_seconds: int,
        filename: str,
        content_type: str,
        expected_byte_size: int | None,
        expected_checksum_sha256: str | None,
    ) -> ExportDownload:
        try:
            storage = self._storages.require(storage_backend)
            if storage.backend != "s3" or self._s3_download_delivery == "redirect":
                redirect_url = storage.presign_download(
                    object_key,
                    expires_seconds=expires_seconds,
                    filename=filename,
                    content_type=content_type,
                    expected_byte_size=expected_byte_size,
                    expected_checksum_sha256=expected_checksum_sha256,
                )
                if redirect_url is not None:
                    return ExportDownload(None, content_type, filename, redirect_url)
            path, remove_after_send = storage.prepare_download(
                object_key,
                expected_byte_size=expected_byte_size,
                expected_checksum_sha256=expected_checksum_sha256,
            )
            return ExportDownload(
                str(path),
                content_type,
                filename,
                remove_after_send=remove_after_send,
            )
        except (OSError, ObjectStorageUnavailableError, ValueError) as exc:
            raise ValueError("Export object is unavailable") from exc


def create_s3_client(settings: Settings) -> Any:
    if settings.s3_access_key_id is None or settings.s3_secret_access_key is None:
        raise ValueError("S3 access credentials are not configured")
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key_id.get_secret_value(),
        aws_secret_access_key=settings.s3_secret_access_key.get_secret_value(),
        config=Config(
            connect_timeout=settings.s3_connect_timeout_seconds,
            read_timeout=settings.s3_read_timeout_seconds,
            retries={"max_attempts": 5, "mode": "standard"},
            s3={"addressing_style": settings.s3_addressing_style},
        ),
    )


def create_object_storage_registry(settings: Settings) -> ObjectStorageRegistry:
    local = LocalObjectStorage(settings.snapshot_storage_path)
    storages: dict[str, ObjectStorage] = {local.backend: local}
    if settings.s3_bucket is not None and settings.s3_access_key_id is not None:
        client = create_s3_client(settings)
        cache = (
            VerifiedObjectCache(
                settings.snapshot_cache_path or settings.snapshot_storage_path / ".object-cache",
                max_bytes=settings.snapshot_cache_max_mb * 1024 * 1024,
            )
            if settings.snapshot_cache_max_mb > 0
            else None
        )
        s3 = S3ObjectStorage(
            client=client,
            bucket=settings.s3_bucket,
            work_root=settings.snapshot_storage_path,
            server_side_encryption=settings.s3_server_side_encryption,
            cache=cache,
        )
        storages[s3.backend] = s3
    return ObjectStorageRegistry(settings.object_storage_backend, storages)
