"""Persist events with business writes and deliver them asynchronously."""

import asyncio
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.messaging.publisher import EventPublisher, open_publisher
from app.models import OutboxEvent
from app.db import session_factory
from app.config import settings
from helpdesk_common import get_logger
from helpdesk_common.events import EventEnvelope

log = get_logger(__name__)


def add_to_outbox(session: AsyncSession, event: EventEnvelope) -> None:
    """Add the event to the caller's transaction; do not publish from HTTP."""
    session.add(
        OutboxEvent(
            id=event.event_id,
            routing_key=event.routing_key,
            payload=event.model_dump(mode="json"),
        )
    )


async def dispatch_outbox(
    publisher: EventPublisher,
    sessions: async_sessionmaker[AsyncSession] = session_factory,
    *,
    interval: float = 1.0,
) -> None:
    """Publish pending rows; event_id lets consumers safely deduplicate crash retries."""
    while True:
        try:
            async with sessions() as session:
                row = await session.scalar(
                    select(OutboxEvent)
                    .where(OutboxEvent.published_at.is_(None))
                    .order_by(OutboxEvent.created_at, OutboxEvent.id)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
                if row is None:
                    await asyncio.sleep(interval)
                    continue
                event = EventEnvelope.model_validate(row.payload)
                await publisher.publish(event)
                row.published_at = datetime.now(UTC)
                await session.commit()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("outbox_delivery_failed")
            try:
                async with sessions.begin() as session:
                    row = await session.scalar(
                        select(OutboxEvent)
                        .where(OutboxEvent.published_at.is_(None))
                        .order_by(OutboxEvent.created_at, OutboxEvent.id)
                        .limit(1)
                        .with_for_update(skip_locked=True)
                    )
                    if row is not None:
                        row.attempts += 1
            except Exception:
                log.exception("outbox_attempt_record_failed")
            await asyncio.sleep(interval)


async def run_outbox_worker() -> None:
    """Keep serving writes while RabbitMQ is unavailable; retry the connection."""
    while True:
        try:
            async with open_publisher(
                host=settings.mq_host,
                port=settings.mq_port,
                login=settings.mq_user,
                password=settings.mq_password.get_secret_value(),
                virtualhost=settings.mq_vhost,
                timeout=settings.mq_timeout,
            ) as publisher:
                await dispatch_outbox(publisher)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("outbox_publisher_connection_failed")
            await asyncio.sleep(2.0)
