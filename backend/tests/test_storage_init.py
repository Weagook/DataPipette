"""Idempotent S3-compatible bucket initialization tests."""

import time
from typing import Any

import pytest
from botocore.exceptions import ClientError  # type: ignore[import-untyped]

from datapipette import storage_init
from datapipette.config import Settings
from datapipette.storage_init import ensure_bucket


class FakeBucketClient:
    def __init__(self, error_code: str = "404") -> None:
        self.exists = False
        self.error_code = error_code
        self.created: dict[str, Any] | None = None
        self.acl: dict[str, Any] | None = None

    def head_bucket(self, *, Bucket: str) -> None:
        if not self.exists:
            raise ClientError(
                {"Error": {"Code": self.error_code, "Message": "hidden"}},
                "HeadBucket",
            )
        assert Bucket == "analytics"

    def create_bucket(self, **values: Any) -> None:
        self.created = values
        self.exists = True

    def put_bucket_acl(self, **values: Any) -> None:
        self.acl = values


def _settings(region: str = "us-east-1") -> Settings:
    return Settings(
        environment="test",
        object_storage_backend="s3",
        s3_region=region,
        s3_bucket="analytics",
        s3_access_key_id="access",
        s3_secret_access_key="secret",
    )


def test_bucket_initialization_is_private_region_aware_and_idempotent() -> None:
    client = FakeBucketClient()
    ensure_bucket(_settings("eu-central-1"), client)
    assert client.created == {
        "Bucket": "analytics",
        "CreateBucketConfiguration": {"LocationConstraint": "eu-central-1"},
    }
    assert client.acl == {"Bucket": "analytics", "ACL": "private"}

    client.created = None
    ensure_bucket(_settings(), client)
    assert client.created is None


def test_bucket_initialization_rejects_bad_configuration_and_auth_errors() -> None:
    with pytest.raises(ValueError):
        ensure_bucket(Settings(environment="test"), FakeBucketClient())
    with pytest.raises(ClientError):
        ensure_bucket(_settings(), FakeBucketClient("AccessDenied"))


def test_storage_init_run_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0

    def initialize(_settings_value: Settings) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("not ready")

    monkeypatch.setattr(storage_init, "get_settings", _settings)
    monkeypatch.setattr(storage_init, "ensure_bucket", initialize)
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    storage_init.run()

    assert attempts == 2


def test_storage_init_run_stops_after_bounded_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storage_init, "get_settings", _settings)
    monkeypatch.setattr(
        storage_init,
        "ensure_bucket",
        lambda _settings_value: (_ for _ in ()).throw(OSError("not ready")),
    )
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    with pytest.raises(RuntimeError, match="did not become ready"):
        storage_init.run()
