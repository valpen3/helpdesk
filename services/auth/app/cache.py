"""Redis-backed login throttling and short-lived profile cache."""

import json
from uuid import UUID

from fastapi import HTTPException, Request
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.config import settings
from app.schemas import Profile
from helpdesk_common import get_logger
from helpdesk_common.redis_client import make_redis_client

log = get_logger(__name__)
PROFILE_TTL = 300


def make_redis() -> Redis:
    return make_redis_client(
        host=settings.redis_host,
        port=settings.redis_port,
        username=settings.redis_user,
        password=settings.redis_password.get_secret_value() or None,
        decode_responses=True,
        socket_connect_timeout=settings.cache_timeout,
        socket_timeout=settings.cache_timeout,
    )


async def ping_redis(client: Redis) -> None:
    await client.ping()


def get_redis(request: Request) -> Redis:
    client: Redis | None = getattr(request.app.state, "redis", None)
    if client is None:
        raise HTTPException(status_code=503, detail="Redis is not ready")
    return client


async def login_locked(redis: Redis, normalized_email: str) -> int | None:
    """Return remaining lock seconds when blocked; Redis failures fail closed."""
    key = f"auth:login_fail:{normalized_email}"
    try:
        count = int(await redis.get(key) or 0)
        if count < settings.login_max_attempts:
            return None
        ttl = await redis.ttl(key)
        return max(int(ttl), 1)
    except RedisError as exc:
        log.warning("login_limiter_unavailable", error=type(exc).__name__)
        raise HTTPException(status_code=503, detail="Login is temporarily unavailable") from exc


async def record_login_failure(redis: Redis, normalized_email: str) -> None:
    key = f"auth:login_fail:{normalized_email}"
    try:
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, settings.login_lock_seconds)
    except RedisError as exc:
        log.warning("login_limiter_unavailable", error=type(exc).__name__)
        raise HTTPException(status_code=503, detail="Login is temporarily unavailable") from exc


async def clear_login_failures(redis: Redis, normalized_email: str) -> None:
    try:
        await redis.delete(f"auth:login_fail:{normalized_email}")
    except RedisError as exc:
        log.warning("login_limiter_unavailable", error=type(exc).__name__)
        raise HTTPException(status_code=503, detail="Login is temporarily unavailable") from exc


async def get_cached_profile(redis: Redis, user_id: UUID) -> Profile | None:
    try:
        cached = await redis.get(f"auth:user:{user_id}")
        return Profile.model_validate_json(cached) if cached else None
    except (RedisError, ValueError) as exc:
        log.warning("profile_cache_read_failed", error=type(exc).__name__)
        return None


async def cache_profile(redis: Redis, profile: Profile) -> None:
    try:
        await redis.set(
            f"auth:user:{profile.id}",
            json.dumps(profile.model_dump(mode="json")),
            ex=PROFILE_TTL,
        )
    except RedisError as exc:
        log.warning("profile_cache_write_failed", error=type(exc).__name__)


async def invalidate_profile(redis: Redis, user_id: UUID) -> None:
    try:
        await redis.delete(f"auth:user:{user_id}")
    except RedisError as exc:
        log.warning("profile_cache_invalidation_failed", error=type(exc).__name__)
