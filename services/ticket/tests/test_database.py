"""Интеграционная проверка на PostgreSQL после `alembic upgrade head`.

Включается явно: RUN_DATABASE_TESTS=1. Использует настройки DB_* и откатывает
тестовые записи, поэтому в обычном `make test` PostgreSQL не требуется.
"""

import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.db import engine, get_session, ping_db, session_factory
from app.models import Comment, Ticket, TicketPriority, TicketStatus

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DATABASE_TESTS") != "1",
    reason="нужна PostgreSQL с применёнными миграциями и RUN_DATABASE_TESTS=1",
)


def test_database_defaults_constraints_and_session_rollback():
    async def check():
        try:
            await ping_db()
            author = uuid4()
            ticket_id = None
            # get_session не делает commit за обработчик запроса.
            async for session in get_session():
                ticket = Ticket(
                    title="Проверка миграции",
                    description="Временная запись",
                    author_id=author,
                    author_email="test@example.com",
                )
                session.add(ticket)
                await session.flush()
                await session.refresh(ticket)
                ticket_id = ticket.id
                assert ticket.status == TicketStatus.OPEN
                assert ticket.priority == TicketPriority.MEDIUM
                assert ticket.version == 1
                assert ticket.escalated is False
                assert ticket.created_at.utcoffset() is not None
                assert ticket.assignee_id is None

                comment = Comment(
                    ticket_id=ticket.id,
                    author_id=author,
                    author_email="test@example.com",
                    author_role="client",
                    body="Комментарий",
                )
                session.add(comment)
                await session.flush()
                await session.refresh(comment)
                assert comment.is_internal is False

                # SAVEPOINT позволяет проверить ошибку, сохранив внешнюю транзакцию.
                with pytest.raises(IntegrityError):
                    async with session.begin_nested():
                        session.add(
                            Comment(
                                ticket_id=-1,
                                author_id=author,
                                author_email="test@example.com",
                                author_role="client",
                                body="Нет такого тикета",
                            )
                        )
                        await session.flush()

                with pytest.raises(DBAPIError):
                    async with session.begin_nested():
                        session.add(
                            Ticket(
                                title="x" * 201,
                                description="Слишком длинная тема",
                                author_id=author,
                                author_email="test@example.com",
                            )
                        )
                        await session.flush()

                await session.delete(ticket)
                await session.flush()
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(Comment)
                        .where(Comment.ticket_id == ticket_id)
                    )
                    == 0
                )

                uncommitted = Ticket(
                    title="Без commit",
                    description="Должно откатиться",
                    author_id=author,
                    author_email="test@example.com",
                )
                session.add(uncommitted)
                await session.flush()
                ticket_id = uncommitted.id

            async with session_factory() as session:
                assert await session.get(Ticket, ticket_id) is None
        finally:
            await engine.dispose()

    asyncio.run(check())
