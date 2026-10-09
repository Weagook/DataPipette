"""Configuration safety behavior."""

from datepipette.config import Settings

PRODUCTION_KEY = "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"


def test_settings_hide_connection_values_in_repr() -> None:
    database_url = "postgresql+psycopg://sensitive-user:sensitive-password@db/datepipette"
    redis_url = "redis://:sensitive-password@redis:6379/0"

    settings = Settings(metadata_database_url=database_url, redis_url=redis_url)

    representation = repr(settings)
    assert "sensitive-password" not in representation
    assert database_url not in representation
    assert redis_url not in representation


def test_api_docs_default_depends_on_environment() -> None:
    assert Settings(environment="development").api_docs_enabled is True
    assert (
        Settings(
            environment="production", connection_encryption_key=PRODUCTION_KEY
        ).api_docs_enabled
        is False
    )
    assert (
        Settings(
            environment="production",
            docs_enabled=True,
            connection_encryption_key=PRODUCTION_KEY,
        ).api_docs_enabled
        is True
    )


def test_production_always_uses_secure_session_cookies() -> None:
    assert Settings(environment="development").effective_session_cookie_secure is False
    assert Settings(
        environment="development", session_cookie_secure=True
    ).effective_session_cookie_secure
    assert Settings(
        environment="production",
        session_cookie_secure=False,
        connection_encryption_key=PRODUCTION_KEY,
    ).effective_session_cookie_secure
