"""Реальные PostgreSQL + Redis: RUN_DATABASE_TESTS=1 и RUN_CACHE_TESTS=1."""

import json
import os
from unittest.mock import AsyncMock

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.cache import EPOCH_KEY, LIST_VERSION_KEY, TicketCache, get_cache, list_key, make_redis
from app.config import settings
from app.main import app
from app.schemas import TicketResponse

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(
        os.environ.get("RUN_DATABASE_TESTS") != "1" or os.environ.get("RUN_CACHE_TESTS") != "1",
        reason="нужны PostgreSQL и Redis",
    ),
]


@pytest.fixture
async def cached_api(api):
    client, headers, sessions = api
    redis = make_redis(settings)
    await redis.ping()
    cache = TicketCache(redis, timeout=1)
    previous = app.dependency_overrides[get_cache]
    app.dependency_overrides[get_cache] = lambda: cache
    try:
        yield client, headers, sessions, cache, redis
    finally:
        app.dependency_overrides[get_cache] = previous
        await redis.aclose()


async def create(client, headers):
    response = await client.post(
        "/api/v1/tickets",
        headers=headers,
        json={"title": "Кэш", "description": "Проверка Redis"},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_miss_hit_change_miss_and_ttl(cached_api):
    client, headers, _, _, redis = cached_api
    ticket = await create(client, headers)
    path = f"/api/v1/tickets/{ticket['id']}"
    first = await client.get(path)
    assert first.headers["X-Cache"] == "MISS"
    assert (await client.get(path)).headers["X-Cache"] == "HIT"
    assert 0 < await redis.ttl(f"ticket:{ticket['id']}") <= 300
    changed = await client.post(path + "/status", json={"status": "closed", "version": 1})
    assert changed.status_code == 200
    assert await redis.get(f"ticket:{ticket['id']}") is None
    fresh = await client.get(path)
    assert fresh.headers["X-Cache"] == "MISS" and fresh.json()["status"] == "closed"
    assert (await client.get(path)).headers["X-Cache"] == "HIT"
    # Принудительное истечение ключа без долгого ожидания.
    await redis.pexpire(f"ticket:{ticket['id']}", 0)
    assert (await client.get(path)).headers["X-Cache"] == "MISS"


async def test_cached_hit_does_not_query_postgres(cached_api, monkeypatch):
    client, headers, sessions, _, _ = cached_api
    ticket = await create(client, headers)
    path = f"/api/v1/tickets/{ticket['id']}"
    await client.get(path)
    query = {"author_id": headers["X-User-Id"]}
    await client.get("/api/v1/tickets", params=query)

    async def no_database(*args, **kwargs):
        raise AssertionError("HIT не должен выполнять запрос к PostgreSQL")

    monkeypatch.setattr(sessions.class_, "get", no_database)
    monkeypatch.setattr(sessions.class_, "scalar", no_database)
    monkeypatch.setattr(sessions.class_, "scalars", no_database)
    assert (await client.get(path)).headers["X-Cache"] == "HIT"
    assert (await client.get("/api/v1/tickets", params=query)).headers["X-Cache"] == "HIT"


async def test_list_versions_and_filter_separation(cached_api):
    client, headers, _, cache, redis = cached_api
    query = {"author_id": headers["X-User-Id"], "status": "open"}
    empty = await client.get("/api/v1/tickets", params=query)
    assert empty.json()["total"] == 0 and empty.headers["X-Cache"] == "MISS"
    assert (await client.get("/api/v1/tickets", params=query)).headers["X-Cache"] == "HIT"
    old_version = await redis.get(LIST_VERSION_KEY)
    ticket = await create(client, headers)
    assert int(await redis.get(LIST_VERSION_KEY)) > int(old_version)
    fresh = await client.get("/api/v1/tickets", params=query)
    assert fresh.headers["X-Cache"] == "MISS" and fresh.json()["total"] == 1
    assert (
        await client.get("/api/v1/tickets", params=dict(reversed(list(query.items()))))
    ).headers["X-Cache"] == "HIT"
    for filters in [
        dict(size=1),
        dict(page=2),
        dict(priority="high"),
        dict(status="closed"),
        dict(author_id="11111111-1111-4111-8111-111111111111"),
        dict(assignee_id="22222222-2222-4222-8222-222222222222"),
        dict(escalated=True),
    ]:
        response = await client.get("/api/v1/tickets", params={**query, **filters})
        assert response.headers["X-Cache"] == "MISS"
    params = dict(
        page=1,
        size=20,
        status="open",
        priority=None,
        author_id=headers["X-User-Id"],
        assignee_id=None,
        escalated=None,
    )
    _, token = await cache.read_list(params)
    key = list_key(params, token)
    assert 0 < await redis.ttl(key) <= 60
    changed = await client.post(
        f"/api/v1/tickets/{ticket['id']}/status", json={"status": "closed", "version": 1}
    )
    assert changed.status_code == 200
    assert await redis.exists(key)  # Старый список не удалён, но больше не читается.
    after = await client.get("/api/v1/tickets", params=query)
    assert after.json()["total"] == 0 and after.headers["X-Cache"] == "MISS"


async def test_bad_json_and_404_are_not_cached(cached_api):
    client, headers, _, cache, redis = cached_api
    ticket = await create(client, headers)
    path = f"/api/v1/tickets/{ticket['id']}"
    await client.get(path)
    for raw in [
        "invalid json",
        "[]",
        json.dumps({"epoch": await redis.get(EPOCH_KEY), "data": {}}),
    ]:
        await redis.set(f"ticket:{ticket['id']}", raw, ex=300)
        response = await client.get(path)
        assert response.status_code == 200 and response.headers["X-Cache"] == "MISS"
    await cache.invalidate(ticket["id"])
    missing = 2**63 - 1
    assert (await client.get(f"/api/v1/tickets/{missing}")).status_code == 404
    assert await redis.get(f"ticket:{missing}") is None


async def test_failed_changes_do_not_invalidate(cached_api):
    client, headers, _, _, redis = cached_api
    ticket = await create(client, headers)
    path = f"/api/v1/tickets/{ticket['id']}"
    await client.get(path)
    version = await redis.get(LIST_VERSION_KEY)
    for payload, expected in [
        ({"status": "resolved", "version": 1}, 422),
        ({"status": "closed", "version": 2}, 409),
    ]:
        assert (await client.post(path + "/status", json=payload)).status_code == expected
    assert await redis.get(LIST_VERSION_KEY) == version
    assert (await client.get(path)).headers["X-Cache"] == "HIT"


async def test_old_database_read_cannot_refill_invalidated_cache(cached_api):
    client, headers, _, cache, redis = cached_api
    ticket = await create(client, headers)
    _, old_token = await cache.read_ticket(ticket["id"])
    path = f"/api/v1/tickets/{ticket['id']}"
    assert (
        await client.post(path + "/status", json={"status": "closed", "version": 1})
    ).status_code == 200
    await cache.store_ticket(TicketResponse.model_validate(ticket), old_token)
    assert await redis.get(f"ticket:{ticket['id']}") is None
    fresh = await client.get(path)
    assert fresh.headers["X-Cache"] == "MISS" and fresh.json()["status"] == "closed"


async def test_redis_outage_writes_and_recovery_discard_old_entries(cached_api, monkeypatch):
    client, headers, _, _, redis = cached_api
    ticket = await create(client, headers)
    path = f"/api/v1/tickets/{ticket['id']}"
    await client.get(path)
    query = {"author_id": headers["X-User-Id"], "status": "open"}
    await client.get("/api/v1/tickets", params=query)
    old_epoch = await redis.get(EPOCH_KEY)
    with monkeypatch.context() as patch:
        patch.setattr(redis, "eval", AsyncMock(side_effect=RedisConnectionError("offline")))
        assert (await client.get(path)).headers["X-Cache"] == "MISS"
        assert (await client.get("/api/v1/tickets", params=query)).status_code == 200
        assert (
            await client.post(path + "/status", json={"status": "closed", "version": 1})
        ).status_code == 200
        assert (await create(client, headers))["id"] != ticket["id"]
    assert await redis.get(f"ticket:{ticket['id']}") is not None  # В Redis осталась старая копия.
    recovered = await client.get(path)
    assert recovered.headers["X-Cache"] == "MISS" and recovered.json()["status"] == "closed"
    assert await redis.get(EPOCH_KEY) != old_epoch
    assert (await client.get(path)).headers["X-Cache"] == "HIT"
    listed = await client.get("/api/v1/tickets", params=query)
    assert listed.headers["X-Cache"] == "MISS" and listed.json()["total"] == 1
