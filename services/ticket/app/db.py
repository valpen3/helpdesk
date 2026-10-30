"""Подключение к БД и отдельная сессия на каждый HTTP-запрос."""

from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(settings.database_url, pool_pre_ping=True)
session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    # Обработчик сам решает, когда делать commit. При выходе незавершённая
    # транзакция откатывается, а соединение возвращается в пул.
    async with session_factory() as session:
        yield session


async def ping_db() -> None:
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
