"""Readiness checks for required runtime dependencies."""

import asyncio
import logging
from collections.abc import Awaitable
from typing import cast

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from datepipette.application.health import DependencyStatus, ReadinessStatus

logger = logging.getLogger(__name__)


class InfrastructureReadinessProbe:
    """Check PostgreSQL and Redis without exposing connection errors."""

    def __init__(
        self,
        database_engine: AsyncEngine,
        redis_client: Redis,
        timeout_seconds: float,
    ) -> None:
        self._database_engine = database_engine
        self._redis_client = redis_client
        self._timeout_seconds = timeout_seconds

    async def check(self) -> ReadinessStatus:
        database, redis = await asyncio.gather(
            self._check_database(),
            self._check_redis(),
        )
        return ReadinessStatus(checks={"metadata_database": database, "redis": redis})

    async def _check_database(self) -> DependencyStatus:
        try:
            async with asyncio.timeout(self._timeout_seconds):
                async with self._database_engine.connect() as connection:
                    await connection.execute(text("SELECT 1"))
        except Exception:
            logger.warning("Metadata database readiness check failed")
            return DependencyStatus(available=False)
        return DependencyStatus(available=True)

    async def _check_redis(self) -> DependencyStatus:
        try:
            async with asyncio.timeout(self._timeout_seconds):
                await cast(Awaitable[bool], self._redis_client.ping())
        except Exception:
            logger.warning("Redis readiness check failed")
            return DependencyStatus(available=False)
        return DependencyStatus(available=True)
