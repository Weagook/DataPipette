"""Lifecycle management for runtime infrastructure clients."""

from dataclasses import dataclass

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from datepipette.config import Settings
from datepipette.infrastructure.database import create_database_engine


@dataclass(slots=True)
class RuntimeResources:
    """External clients owned by the API process."""

    database_engine: AsyncEngine
    redis_client: Redis

    async def close(self) -> None:
        await self.redis_client.aclose()
        await self.database_engine.dispose()


def create_runtime_resources(settings: Settings) -> RuntimeResources:
    """Create lazy infrastructure clients without connecting at startup."""

    return RuntimeResources(
        database_engine=create_database_engine(settings),
        redis_client=Redis.from_url(settings.redis_url.get_secret_value(), decode_responses=True),
    )
