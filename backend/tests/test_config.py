"""Configuration safety behavior."""

from typing import Any

import pytest
from pydantic import ValidationError

from datepipette.config import Settings

PRODUCTION_KEY = "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"


def _production_settings(**values: Any) -> Settings:
    return Settings(
        environment="production",
        connection_encryption_key=PRODUCTION_KEY,
        object_storage_backend="s3",
        s3_bucket="datepipette",
        s3_access_key_id="access-key",
        s3_secret_access_key="secret-key",
        **values,
    )


def test_settings_hide_connection_values_in_repr() -> None:
    database_url = "postgresql+psycopg://sensitive-user:sensitive-password@db/datepipette"
    redis_url = "redis://:sensitive-password@redis:6379/0"

    settings = Settings(
        metadata_database_url=database_url,
        redis_url=redis_url,
        s3_bucket="datepipette",
        s3_access_key_id="sensitive-access",
        s3_secret_access_key="sensitive-s3-secret",
    )

    representation = repr(settings)
    assert "sensitive-password" not in representation
    assert database_url not in representation
    assert redis_url not in representation
    assert "sensitive-access" not in representation
    assert "sensitive-s3-secret" not in representation


def test_api_docs_default_depends_on_environment() -> None:
    assert Settings(environment="development").api_docs_enabled is True
    assert _production_settings().api_docs_enabled is False
    assert _production_settings(docs_enabled=True).api_docs_enabled is True


def test_production_always_uses_secure_session_cookies() -> None:
    assert Settings(environment="development").effective_session_cookie_secure is False
    assert Settings(
        environment="development", session_cookie_secure=True
    ).effective_session_cookie_secure


def test_installation_timezone_requires_an_iana_name() -> None:
    assert Settings(installation_timezone="Asia/Almaty").installation_timezone == "Asia/Almaty"
    with pytest.raises(ValidationError):
        Settings(installation_timezone="Mars/Olympus")
    assert _production_settings(session_cookie_secure=False).effective_session_cookie_secure


def test_s3_configuration_is_complete_and_production_safe() -> None:
    with pytest.raises(ValidationError, match="S3 storage requires"):
        Settings(object_storage_backend="s3")
    with pytest.raises(ValidationError, match="requires S3-compatible"):
        Settings(environment="production", connection_encryption_key=PRODUCTION_KEY)
    with pytest.raises(ValidationError, match="must use HTTPS"):
        _production_settings(s3_endpoint_url="http://minio:9000")
    assert _production_settings(s3_endpoint_url="https://objects.example").s3_bucket == (
        "datepipette"
    )


def test_cleanup_defaults_are_bounded_and_require_a_safe_orphan_grace() -> None:
    settings = Settings(environment="test")
    assert settings.cleanup_enabled is True
    assert settings.cleanup_batch_size == 100
    assert settings.cleanup_snapshot_grace_seconds == 3600
    assert settings.cleanup_export_grace_seconds == 300
    assert settings.cleanup_orphan_grace_seconds == 86_400
    with pytest.raises(ValidationError):
        Settings(cleanup_orphan_grace_seconds=3599)


def test_refresh_retry_and_cancellation_defaults_are_bounded() -> None:
    settings = Settings(environment="test")
    assert settings.refresh_max_attempts == 3
    assert settings.refresh_retry_base_seconds == 5
    assert settings.refresh_retry_max_seconds == 300
    assert settings.refresh_cancel_poll_seconds == 1.0
    assert settings.refresh_run_lease_seconds == 30
    with pytest.raises(ValidationError):
        Settings(refresh_max_attempts=0)
    with pytest.raises(ValidationError):
        Settings(refresh_cancel_poll_seconds=0)
    with pytest.raises(ValidationError):
        Settings(refresh_run_lease_seconds=9)
    with pytest.raises(ValidationError, match="must exceed"):
        Settings(refresh_cancel_poll_seconds=10, refresh_run_lease_seconds=10)


def test_query_safety_thresholds_are_bounded_and_ordered() -> None:
    settings = Settings(environment="test")
    assert settings.query_safety_estimated_rows_warning == 1_000_000
    assert settings.query_safety_estimated_rows_block == 10_000_000
    assert settings.query_safety_total_cost_warning == 100_000
    assert settings.query_safety_join_count_block == 9
    with pytest.raises(ValidationError, match="row warning"):
        Settings(
            query_safety_estimated_rows_warning=100,
            query_safety_estimated_rows_block=100,
        )
    with pytest.raises(ValidationError, match="cost warning"):
        Settings(
            query_safety_total_cost_warning=100,
            query_safety_total_cost_block=50,
        )
    with pytest.raises(ValidationError, match="join warning"):
        Settings(query_safety_join_count_warning=5, query_safety_join_count_block=5)
