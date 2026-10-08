"""Application configuration loaded from environment variables."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    metadata_database_url: SecretStr = SecretStr(
        "postgresql+psycopg://datepipette:datepipette_dev_only@localhost:5432/datepipette"
    )
    redis_url: SecretStr = SecretStr("redis://localhost:6379/0")

    @property
    def api_docs_enabled(self) -> bool:
        """Enable interactive API docs by default only during development."""

        if self.docs_enabled is not None:
            return self.docs_enabled
        return self.environment == "development"


@lru_cache
def get_settings() -> Settings:
    """Return one immutable-by-convention settings instance per process."""

    return Settings()
