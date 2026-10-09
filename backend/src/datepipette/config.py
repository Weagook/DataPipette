"""Application configuration loaded from environment variables."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
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
    connection_encryption_key: SecretStr = SecretStr(DEVELOPMENT_CONNECTION_ENCRYPTION_KEY)
    metadata_database_url: SecretStr = SecretStr(
        "postgresql+psycopg://datepipette:datepipette_dev_only@localhost:5432/datepipette"
    )
    redis_url: SecretStr = SecretStr("redis://localhost:6379/0")

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
