"""Полные HTTP-запросы с настоящим PostgreSQL; включение RUN_DATABASE_TESTS=1."""

import asyncio
import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update

from app.models import Ticket

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(os.environ.get("RUN_DATABASE_TESTS") != "1", reason="нужна тестовая БД"),
]


async def create(client, headers, **fields):
    response = await client.post(
        "/api/v1/tickets",
        headers=headers,
        json={"title": "  Не работает VPN  ", "description": "  Нужна помощь  ", **fields},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_create_and_read_committed_ticket(api):
    client, headers, sessions = api
    ticket = await create(client, headers)
    assert ticket["title"] == "Не работает VPN"
    assert ticket["description"] == "Нужна помощь"
    assert ticket["author_id"] == headers["X-User-Id"]
    assert ticket["author_email"] == headers["X-User-Email"]
    assert ticket["status"] == "open" and ticket["priority"] == "medium"
    assert ticket["version"] == 1 and ticket["assignee_id"] is None
    response = await client.get(f"/api/v1/tickets/{ticket['id']}")
    assert response.status_code == 200
    assert response.json() == ticket
    async with sessions() as session:
        saved = await session.get(Ticket, ticket["id"])
        assert saved is not None and saved.title == ticket["title"]


async def test_list_pagination_filters_and_stable_sort(api):
    client, headers, sessions = api
    first = await create(client, headers, priority="low")
    second = await create(client, headers, priority="high")
    assignee = uuid4()
    # Одинаковое время специально проверяет второй ключ сортировки — id.
    async with sessions() as session:
        await session.execute(
            update(Ticket)
            .where(Ticket.id.in_([first["id"], second["id"]]))
            .values(created_at=datetime(2026, 1, 1, tzinfo=UTC))
        )
        await session.execute(
            update(Ticket)
            .where(Ticket.id == second["id"])
            .values(assignee_id=assignee, escalated=True)
        )
        await session.commit()
    query = {"author_id": headers["X-User-Id"], "size": 1}
    page1 = (await client.get("/api/v1/tickets", params=query)).json()
    page2 = (await client.get("/api/v1/tickets", params={**query, "page": 2})).json()
    page3 = (await client.get("/api/v1/tickets", params={**query, "page": 3})).json()
    assert page1["total"] == page2["total"] == page3["total"] == 2
    assert page1["items"][0]["id"] == second["id"]
    assert page2["items"][0]["id"] == first["id"]
    assert page3["items"] == []
    for filters, expected in [
        ({"priority": "high", "status": "open", "escalated": True}, second["id"]),
        ({"assignee_id": str(assignee)}, second["id"]),
        ({"escalated": False}, first["id"]),
    ]:
        response = await client.get("/api/v1/tickets", params={**query, **filters})
        assert response.status_code == 200
        assert response.json()["total"] == 1
        assert response.json()["items"][0]["id"] == expected


async def test_missing_ticket(api):
    client, _, sessions = api
    async with sessions() as session:
        missing = (await session.scalar(select(func.max(Ticket.id))) or 0) + 1
    assert (await client.get(f"/api/v1/tickets/{missing}")).status_code == 404
    assert (
        await client.post(
            f"/api/v1/tickets/{missing}/status", json={"status": "closed", "version": 1}
        )
    ).status_code == 404


async def test_transitions_versions_and_dates(api):
    client, headers, _ = api
    ticket = await create(client, headers)
    path = f"/api/v1/tickets/{ticket['id']}/status"
    for status in [
        "in_progress",
        "waiting_client",
        "in_progress",
        "resolved",
        "in_progress",
        "resolved",
        "closed",
    ]:
        response = await client.post(path, json={"status": status, "version": ticket["version"]})
        assert response.status_code == 200, response.text
        changed = response.json()
        assert changed["version"] == ticket["version"] + 1
        assert changed["status"] == status
        assert changed["created_at"] == ticket["created_at"]
        if status == "resolved":
            assert changed["resolved_at"] is not None
        if status == "in_progress":
            assert changed["resolved_at"] is None and changed["closed_at"] is None
        if status == "closed":
            assert changed["closed_at"] is not None and changed["resolved_at"] is not None
        ticket = changed
    assert (
        await client.post(path, json={"status": "in_progress", "version": 1})
    ).status_code == 409
    assert (
        await client.post(path, json={"status": "open", "version": ticket["version"]})
    ).status_code == 422


async def test_invalid_transition_does_not_change_database(api):
    client, headers, _ = api
    ticket = await create(client, headers)
    path = f"/api/v1/tickets/{ticket['id']}"
    response = await client.post(path + "/status", json={"status": "resolved", "version": 1})
    assert response.status_code == 422
    assert (await client.get(path)).json() == ticket


async def test_concurrent_changes_have_one_winner(api, monkeypatch):
    client, headers, sessions = api
    ticket = await create(client, headers)
    both_read = asyncio.Event()
    readers = 0
    original_get = sessions.class_.get

    async def synchronized_get(session, entity, identity, **kwargs):
        nonlocal readers
        result = await original_get(session, entity, identity, **kwargs)
        if entity is Ticket and identity == ticket["id"]:
            readers += 1
            if readers == 2:
                both_read.set()
            # Оба запроса гарантированно читают version=1 до первого UPDATE.
            await asyncio.wait_for(both_read.wait(), timeout=10)
        return result

    monkeypatch.setattr(sessions.class_, "get", synchronized_get)
    responses = await asyncio.gather(
        *[
            client.post(
                f"/api/v1/tickets/{ticket['id']}/status", json={"status": status, "version": 1}
            )
            for status in ["closed", "in_progress"]
        ]
    )
    assert sorted(response.status_code for response in responses) == [200, 409]
    saved = (await client.get(f"/api/v1/tickets/{ticket['id']}")).json()
    assert saved["version"] == 2


async def test_cancel_open_ticket(api):
    client, headers, _ = api
    ticket = await create(client, headers)
    response = await client.post(
        f"/api/v1/tickets/{ticket['id']}/status", json={"status": "closed", "version": 1}
    )
    assert response.status_code == 200
    assert response.json()["closed_at"] is not None
    assert response.json()["resolved_at"] is None
