"""Application configuration loaded from environment variables."""

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEVELOPMENT_CONNECTION_ENCRYPTION_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


class Settings(BaseSettings):
    """Runtime settings with safe local-development defaults."""

    model_config = SettingsConfigDict(
        env_prefix="DATEPIPETTE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    docs_enabled: bool | None = None
    readiness_timeout_seconds: float = Field(default=2.0, gt=0, le=30)
    session_ttl_seconds: int = Field(default=43_200, ge=300, le=604_800)
    session_cookie_secure: bool | None = None
    login_rate_limit_attempts: int = Field(default=5, ge=1, le=100)
    login_rate_limit_window_seconds: int = Field(default=900, ge=60, le=86_400)
    source_connect_timeout_seconds: int = Field(default=5, ge=1, le=30)
    source_statement_timeout_seconds: int = Field(default=5, ge=1, le=60)
    query_safety_estimated_rows_warning: int = Field(default=1_000_000, ge=1)
    query_safety_estimated_rows_block: int = Field(default=10_000_000, ge=2)
    query_safety_total_cost_warning: float = Field(default=100_000.0, gt=0)
    query_safety_total_cost_block: float = Field(default=1_000_000.0, gt=0)
    query_safety_join_count_warning: int = Field(default=5, ge=1, le=63)
    query_safety_join_count_block: int = Field(default=9, ge=2, le=64)
    query_safety_sequential_scan_rows_warning: int = Field(default=250_000, ge=1)
    query_safety_sort_rows_warning: int = Field(default=500_000, ge=1)
    query_safety_stale_statistics_days: int = Field(default=30, ge=1, le=3650)
    refresh_statement_timeout_seconds: int = Field(default=1800, ge=1, le=86_400)
    refresh_batch_rows: int = Field(default=50_000, ge=1_000, le=100_000)
    refresh_max_attempts: int = Field(default=3, ge=1, le=10)
    refresh_retry_base_seconds: int = Field(default=5, ge=1, le=3600)
    refresh_retry_max_seconds: int = Field(default=300, ge=1, le=86_400)
    refresh_cancel_poll_seconds: float = Field(default=1.0, ge=0.1, le=10.0)
    refresh_run_lease_seconds: int = Field(default=30, ge=10, le=300)
    installation_timezone: str = "UTC"
    scheduler_poll_seconds: float = Field(default=15.0, ge=1.0, le=300.0)
    scheduler_batch_size: int = Field(default=100, ge=1, le=1000)
    object_storage_backend: Literal["local", "s3"] = "local"
    snapshot_storage_path: Path = Path(".local/snapshots")
    s3_endpoint_url: str | None = None
    s3_region: str = "us-east-1"
    s3_bucket: str | None = None
    s3_access_key_id: SecretStr | None = None
    s3_secret_access_key: SecretStr | None = None
    s3_addressing_style: Literal["path", "virtual"] = "path"
    s3_server_side_encryption: Literal["AES256"] | None = None
    s3_connect_timeout_seconds: int = Field(default=5, ge=1, le=60)
    s3_read_timeout_seconds: int = Field(default=60, ge=1, le=600)
    cleanup_enabled: bool = True
    cleanup_interval_seconds: int = Field(default=300, ge=60, le=86_400)
    cleanup_batch_size: int = Field(default=100, ge=1, le=1000)
    cleanup_snapshot_grace_seconds: int = Field(default=3600, ge=60, le=604_800)
    cleanup_export_grace_seconds: int = Field(default=300, ge=60, le=86_400)
    cleanup_orphan_grace_seconds: int = Field(default=86_400, ge=3600, le=2_592_000)
    snapshot_query_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    snapshot_query_memory_limit_mb: int = Field(default=256, ge=64, le=4096)
    snapshot_query_threads: int = Field(default=2, ge=1, le=16)
    snapshot_query_concurrency: int = Field(default=4, ge=1, le=64)
    snapshot_query_temp_limit_mb: int = Field(default=512, ge=64, le=8192)
    snapshot_query_max_response_bytes: int = Field(
        default=2_097_152,
        ge=65_536,
        le=16_777_216,
    )
    export_memory_limit_mb: int = Field(default=512, ge=64, le=4096)
    export_threads: int = Field(default=2, ge=1, le=16)
    export_temp_limit_mb: int = Field(default=2048, ge=64, le=32768)
    export_max_rows: int = Field(default=5_000_000, ge=1, le=10_000_000)
    export_fetch_rows: int = Field(default=10_000, ge=100, le=100_000)
    connection_encryption_key: SecretStr = SecretStr(DEVELOPMENT_CONNECTION_ENCRYPTION_KEY)
    metadata_database_url: SecretStr = SecretStr(
        "postgresql+psycopg://datepipette:datepipette_dev_only@localhost:5432/datepipette"
    )
    redis_url: SecretStr = SecretStr("redis://localhost:6379/0")

    @field_validator("installation_timezone")
    @classmethod
    def validate_installation_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Installation timezone must be a valid IANA name") from exc
        return value

    @model_validator(mode="after")
    def reject_development_encryption_key_in_production(self) -> "Settings":
        if (
            self.environment == "production"
            and self.connection_encryption_key.get_secret_value()
            == DEVELOPMENT_CONNECTION_ENCRYPTION_KEY
        ):
            raise ValueError("Production requires an explicit connection encryption key")
        s3_values = (
            self.s3_bucket,
            self.s3_access_key_id,
            self.s3_secret_access_key,
        )
        configured_s3_values = sum(value is not None for value in s3_values)
        if configured_s3_values not in {0, len(s3_values)}:
            raise ValueError("S3 bucket and access credentials must be configured together")
        if self.object_storage_backend == "s3" and configured_s3_values != len(s3_values):
            raise ValueError("S3 storage requires bucket and access credentials")
        if self.environment == "production" and self.object_storage_backend != "s3":
            raise ValueError("Production requires S3-compatible object storage")
        if self.environment == "production" and self.s3_endpoint_url is not None:
            endpoint = urlparse(self.s3_endpoint_url)
            if endpoint.scheme != "https" or not endpoint.netloc:
                raise ValueError("Production S3 endpoints must use HTTPS")
        if self.refresh_run_lease_seconds <= self.refresh_cancel_poll_seconds:
            raise ValueError("Refresh run lease must exceed the heartbeat polling interval")
        if self.query_safety_estimated_rows_warning >= self.query_safety_estimated_rows_block:
            raise ValueError("Query safety row warning threshold must be below block threshold")
        if self.query_safety_total_cost_warning >= self.query_safety_total_cost_block:
            raise ValueError("Query safety cost warning threshold must be below block threshold")
        if self.query_safety_join_count_warning >= self.query_safety_join_count_block:
            raise ValueError("Query safety join warning threshold must be below block threshold")
        return self

    @property
    def api_docs_enabled(self) -> bool:
        """Enable interactive API docs by default only during development."""

        if self.docs_enabled is not None:
            return self.docs_enabled
        return self.environment == "development"

    @property
    def effective_session_cookie_secure(self) -> bool:
        """Never allow insecure browser-session cookies in production."""

        if self.environment == "production":
            return True
        return bool(self.session_cookie_secure)


@lru_cache
def get_settings() -> Settings:
    """Return one immutable-by-convention settings instance per process."""

    return Settings()
