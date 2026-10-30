"""Cache-aside: БД остаётся источником данных, Redis можно временно потерять."""

import asyncio
import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any
from uuid import uuid4

from fastapi import Request
from pydantic import BaseModel, ValidationError
from redis.asyncio import Redis
from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from redis.retry import Retry

from app.config import Settings
from app.schemas import TicketListResponse, TicketResponse
from helpdesk_common import get_logger

log = get_logger(__name__)
EPOCH_KEY = "tickets:cache_epoch"
LIST_VERSION_KEY = "tickets:list_version"
TICKET_TTL = 300
LIST_TTL = 60

# Каждый Lua-скрипт выполняется Redis целиком, без вмешательства других запросов.
STATE = """
if ARGV[2] == '1' then
    redis.call('SET', KEYS[1], ARGV[1])
    redis.call('SET', KEYS[2], '0')
else
    redis.call('SET', KEYS[1], ARGV[1], 'NX')
    redis.call('SET', KEYS[2], '0', 'NX')
end
return {redis.call('GET', KEYS[1]), redis.call('GET', KEYS[2])}
"""
READ = """
if redis.call('GET', KEYS[1]) ~= ARGV[1]
    or redis.call('GET', KEYS[2]) ~= ARGV[2] then return nil end
return redis.call('GET', KEYS[3])
"""
STORE = """
if redis.call('GET', KEYS[1]) ~= ARGV[1]
    or redis.call('GET', KEYS[2]) ~= ARGV[2] then return 0 end
redis.call('SET', KEYS[3], ARGV[3], 'EX', ARGV[4])
return 1
"""
INVALIDATE = """
redis.call('DEL', KEYS[1])
return redis.call('INCR', KEYS[2])
"""


@dataclass(frozen=True)
class CacheToken:
    epoch: str
    version: str


def list_key(params: dict[str, Any], token: CacheToken) -> str:
    normalized = json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)
    digest = sha256(normalized.encode()).hexdigest()
    return f"tickets:list:v{token.version}:{digest}"


def make_redis(settings: Settings) -> Redis:
    return Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        db=settings.redis_db,
        username=settings.redis_user,
        password=settings.redis_password.get_secret_value() or None,
        decode_responses=True,
        socket_connect_timeout=settings.cache_timeout,
        socket_timeout=settings.cache_timeout,
        retry=Retry(NoBackoff(), 0),
    )


class TicketCache:
    def __init__(self, client: Redis | None, *, timeout: float = 0.25):
        self.client = client
        self.timeout = timeout
        # Новый процесс и первое обращение после сбоя начинают новую эпоху кэша.
        self._recover = True
        self._failures = 0
        self._state_lock = asyncio.Lock()

    async def _eval(self, script: str, keys: list[str], args: list[Any]) -> Any:
        if self.client is None:
            return None
        try:
            async with asyncio.timeout(self.timeout):
                return await self.client.eval(script, len(keys), *keys, *args)
        except (RedisError, TimeoutError, OSError, UnicodeError) as exc:
            self._recover = True
            self._failures += 1
            log.warning("cache_unavailable", error=type(exc).__name__)
            return None

    async def _state(self) -> CacheToken | None:
        if self.client is None:
            return None
        try:
            # Ограничиваем и ожидание lock: при сбое сотня запросов не должна
            # по очереди ждать сотню сетевых таймаутов.
            async with asyncio.timeout(self.timeout):
                async with self._state_lock:
                    failures = self._failures
                    result = await self._eval(
                        STATE,
                        [EPOCH_KEY, LIST_VERSION_KEY],
                        [uuid4().hex, "1" if self._recover else "0"],
                    )
                    if result is None:
                        return None
                    self._recover = self._failures != failures
                    if self._recover:
                        return None
                    return CacheToken(*result)
        except TimeoutError:
            self._recover = True
            self._failures += 1
            log.warning("cache_unavailable", error="TimeoutError")
            return None

    async def _read(self, key: str, token: CacheToken, model: type[BaseModel]) -> Any:
        raw = await self._eval(
            READ, [EPOCH_KEY, LIST_VERSION_KEY, key], [token.epoch, token.version]
        )
        if raw is None:
            return None
        try:
            entry = json.loads(raw)
            if not isinstance(entry, dict) or entry.get("epoch") != token.epoch:
                return None
            return model.model_validate(entry.get("data"))
        except (ValueError, TypeError, ValidationError) as exc:
            log.warning("cache_invalid_entry", key=key, error=type(exc).__name__)
            return None

    async def _store(self, key: str, data: BaseModel, token: CacheToken | None, ttl: int) -> None:
        if token is None:
            return
        entry = json.dumps({"epoch": token.epoch, "data": data.model_dump(mode="json")})
        await self._eval(
            STORE, [EPOCH_KEY, LIST_VERSION_KEY, key], [token.epoch, token.version, entry, ttl]
        )

    async def read_ticket(self, ticket_id: int) -> tuple[TicketResponse | None, CacheToken | None]:
        token = await self._state()
        cached = await self._read(f"ticket:{ticket_id}", token, TicketResponse) if token else None
        return cached, token

    async def store_ticket(self, data: TicketResponse, token: CacheToken | None) -> None:
        await self._store(f"ticket:{data.id}", data, token, TICKET_TTL)

    async def read_list(
        self, params: dict[str, Any]
    ) -> tuple[TicketListResponse | None, CacheToken | None]:
        token = await self._state()
        cached = (
            await self._read(list_key(params, token), token, TicketListResponse) if token else None
        )
        return cached, token

    async def store_list(
        self, params: dict[str, Any], data: TicketListResponse, token: CacheToken | None
    ) -> None:
        if token is not None:
            await self._store(list_key(params, token), data, token, LIST_TTL)

    async def invalidate(self, ticket_id: int) -> None:
        if await self._state() is not None:
            await self._eval(INVALIDATE, [f"ticket:{ticket_id}", LIST_VERSION_KEY], [])


disabled_cache = TicketCache(None)


def get_cache(request: Request) -> TicketCache:
    # В обычных unit-тестах ASGI lifespan не запускается и сеть не нужна.
    return getattr(request.app.state, "ticket_cache", disabled_cache)
