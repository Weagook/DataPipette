"""Application configuration loaded from environment variables."""

from functools import lru_cache
from pathlib import Path
from typing import Literal
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
    refresh_statement_timeout_seconds: int = Field(default=1800, ge=1, le=86_400)
    refresh_batch_rows: int = Field(default=50_000, ge=1_000, le=100_000)
    installation_timezone: str = "UTC"
    scheduler_poll_seconds: float = Field(default=15.0, ge=1.0, le=300.0)
    scheduler_batch_size: int = Field(default=100, ge=1, le=1000)
    snapshot_storage_path: Path = Path(".local/snapshots")
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
