"""Необязательный кэш не должен ломать запрос при ошибке или зависании Redis."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.cache import TicketCache


@pytest.mark.parametrize(
    "error",
    [
        RedisConnectionError("offline"),
        TimeoutError(),
        UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid"),
    ],
)
def test_redis_failure_is_a_cache_miss(error):
    cache = TicketCache(SimpleNamespace(eval=AsyncMock(side_effect=error)))

    async def check():
        assert await cache.read_ticket(1) == (None, None)
        assert await cache.read_list({"page": 1}) == (None, None)
        await cache.invalidate(1)

    asyncio.run(check())


def test_hanging_redis_has_a_deadline():
    async def hanging_eval(*args):
        await asyncio.sleep(10)

    async def check():
        cache = TicketCache(SimpleNamespace(eval=hanging_eval), timeout=0.01)
        results = await asyncio.wait_for(
            asyncio.gather(*(cache.read_ticket(1) for _ in range(100))), timeout=0.5
        )
        assert results == [(None, None)] * 100

    asyncio.run(check())
