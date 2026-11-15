"""Общее HTTP-окружение для тестов PostgreSQL и кэша."""

from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.cache import TicketCache, get_cache
from app.config import settings
from app.db import get_session
from app.main import app
from app.models import Ticket


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def api():
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    author = uuid4()

    async def test_session():
        async with sessions() as session:
            yield session

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_session] = test_session
    app.dependency_overrides[get_cache] = lambda: TicketCache(None)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client, {"X-User-Id": str(author), "X-User-Email": "client@example.com"}, sessions
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        async with sessions() as session:
            await session.execute(delete(Ticket).where(Ticket.author_id == author))
            await session.commit()
        await engine.dispose()
