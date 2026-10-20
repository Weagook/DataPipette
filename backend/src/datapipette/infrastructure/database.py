"""Metadata database resources."""

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from datapipette.config import Settings


class Base(DeclarativeBase):
    """Base for DataPipette-owned metadata models."""

    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


metadata = Base.metadata


def create_database_engine(settings: Settings) -> AsyncEngine:
    """Create a lazy async engine for DataPipette-owned metadata."""

    return create_async_engine(
        settings.metadata_database_url.get_secret_value(),
        pool_pre_ping=True,
    )


def create_advisory_lock_engine(settings: Settings) -> AsyncEngine:
    """Create isolated sessions whose lifetime owns distributed advisory locks."""

    return create_async_engine(
        settings.metadata_database_url.get_secret_value(),
        poolclass=NullPool,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create short-lived transaction sessions for metadata operations."""

    return async_sessionmaker(engine, expire_on_commit=False)
