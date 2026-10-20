"""Redis-backed login throttling."""

from collections.abc import Awaitable
from typing import cast

from redis.asyncio import Redis

_CHECK_SCRIPT = """
local account_count = tonumber(redis.call('GET', KEYS[1]) or '0')
local client_count = tonumber(redis.call('GET', KEYS[2]) or '0')
if account_count >= tonumber(ARGV[1]) or client_count >= tonumber(ARGV[1]) then
  return math.max(redis.call('TTL', KEYS[1]), redis.call('TTL', KEYS[2]), 1)
end
return 0
"""

_FAILURE_SCRIPT = """
local account_count = redis.call('INCR', KEYS[1])
if account_count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[2]) end
local client_count = redis.call('INCR', KEYS[2])
if client_count == 1 then redis.call('EXPIRE', KEYS[2], ARGV[2]) end
if account_count >= tonumber(ARGV[1]) or client_count >= tonumber(ARGV[1]) then
  return math.max(redis.call('TTL', KEYS[1]), redis.call('TTL', KEYS[2]), 1)
end
return 0
"""


class RedisLoginRateLimiter:
    def __init__(self, redis: Redis, attempts: int, window_seconds: int) -> None:
        self._redis = redis
        self._attempts = attempts
        self._window_seconds = window_seconds

    def _keys(self, account_key: str, client_key: str) -> tuple[str, str]:
        return (
            f"datapipette:login:account:{account_key}",
            f"datapipette:login:client:{client_key}",
        )

    async def retry_after(self, account_key: str, client_key: str) -> int | None:
        result = await cast(
            Awaitable[int],
            self._redis.eval(
                _CHECK_SCRIPT,
                2,
                *self._keys(account_key, client_key),
                str(self._attempts),
            ),
        )
        return result or None

    async def record_failure(self, account_key: str, client_key: str) -> int | None:
        result = await cast(
            Awaitable[int],
            self._redis.eval(
                _FAILURE_SCRIPT,
                2,
                *self._keys(account_key, client_key),
                str(self._attempts),
                str(self._window_seconds),
            ),
        )
        return result or None

    async def reset_account(self, account_key: str) -> None:
        account_redis_key, _ = self._keys(account_key, "unused")
        await cast(Awaitable[int], self._redis.delete(account_redis_key))
