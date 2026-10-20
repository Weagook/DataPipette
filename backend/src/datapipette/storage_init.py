"""Idempotently initialize local S3-compatible development storage."""

import time
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]

from datapipette.config import Settings, get_settings
from datapipette.infrastructure.object_storage import create_s3_client


def ensure_bucket(settings: Settings, client: Any | None = None) -> None:
    if settings.object_storage_backend != "s3" or settings.s3_bucket is None:
        raise ValueError("S3 object storage must be configured")
    resolved_client = client or create_s3_client(settings)
    try:
        resolved_client.head_bucket(Bucket=settings.s3_bucket)
        return
    except ClientError as exc:
        error = exc.response.get("Error", {})
        if str(error.get("Code")) not in {"404", "NoSuchBucket", "NotFound"}:
            raise
    parameters: dict[str, Any] = {"Bucket": settings.s3_bucket}
    if settings.s3_region != "us-east-1":
        parameters["CreateBucketConfiguration"] = {"LocationConstraint": settings.s3_region}
    resolved_client.create_bucket(**parameters)
    resolved_client.put_bucket_acl(Bucket=settings.s3_bucket, ACL="private")


def run() -> None:
    settings = get_settings()
    last_error: Exception | None = None
    for _attempt in range(30):
        try:
            ensure_bucket(settings)
            return
        except (BotoCoreError, OSError) as exc:
            last_error = exc
            time.sleep(1)
    raise RuntimeError("S3-compatible storage did not become ready") from last_error


if __name__ == "__main__":
    run()
