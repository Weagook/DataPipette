"""Lifecycle management for runtime infrastructure clients."""

from dataclasses import dataclass

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from datapipette.config import Settings
from datapipette.infrastructure.database import (
    create_advisory_lock_engine,
    create_database_engine,
    create_session_factory,
)


@dataclass(slots=True)
class RuntimeResources:
    """External clients owned by the API process."""

    database_engine: AsyncEngine
    advisory_lock_engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    redis_client: Redis

    async def close(self) -> None:
        await self.redis_client.aclose()
        await self.advisory_lock_engine.dispose()
        await self.database_engine.dispose()


def create_runtime_resources(settings: Settings) -> RuntimeResources:
    """Create lazy infrastructure clients without connecting at startup."""

    database_engine = create_database_engine(settings)
    return RuntimeResources(
        database_engine=database_engine,
        advisory_lock_engine=create_advisory_lock_engine(settings),
        session_factory=create_session_factory(database_engine),
        redis_client=Redis.from_url(settings.redis_url.get_secret_value(), decode_responses=True),
    )
