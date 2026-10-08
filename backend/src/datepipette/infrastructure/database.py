"""Metadata database resources."""

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from datepipette.config import Settings

metadata = MetaData()


def create_database_engine(settings: Settings) -> AsyncEngine:
    """Create a lazy async engine for DatePipette-owned metadata."""

    return create_async_engine(
        settings.metadata_database_url.get_secret_value(),
        pool_pre_ping=True,
    )
