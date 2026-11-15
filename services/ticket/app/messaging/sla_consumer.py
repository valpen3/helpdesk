"""Consume SLA breach events and apply each event at most once."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import aio_pika
from aio_pika.abc import AbstractRobustConnection
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.cache import TicketCache
from app.db import session_factory
from app.models import ProcessedEvent, Ticket, TicketHistory, TicketPriority
from helpdesk_common import get_logger
from helpdesk_common.broker import connect_broker
from helpdesk_common.events import EventEnvelope, EventType

log = get_logger(__name__)

PRIORITY_ORDER = [
    TicketPriority.LOW,
    TicketPriority.MEDIUM,
    TicketPriority.HIGH,
    TicketPriority.CRITICAL,
]


def _required(data: dict[str, Any], key: str) -> Any:
    if key not in data:
        raise ValueError(f"SLA event is missing {key}")
    return data[key]


async def apply_sla_breach(event: EventEnvelope, cache: TicketCache) -> bool:
    """Commit the idempotency marker and escalation atomically; return whether applied."""
    if event.event_type != EventType.TICKET_SLA_BREACHED:
        raise ValueError("expected ticket.sla_breached")
    raw_ticket_id = _required(event.data, "ticket_id")
    if (
        isinstance(raw_ticket_id, bool)
        or not isinstance(raw_ticket_id, int)
        or raw_ticket_id < 1
    ):
        raise ValueError("ticket_id must be a positive integer")
    ticket_id = raw_ticket_id
    kind = str(_required(event.data, "kind"))
    _required(event.data, "title")
    due_at = datetime.fromisoformat(str(_required(event.data, "due_at")).replace("Z", "+00:00"))
    if due_at.tzinfo is None:
        raise ValueError("due_at must include a timezone")
    TicketPriority(str(_required(event.data, "priority")))
    if kind not in {"response", "resolution"}:
        raise ValueError("kind must be response or resolution")

    async with session_factory() as session:
        async with session.begin():
            inserted = await session.scalar(
                insert(ProcessedEvent)
                .values(event_id=event.event_id, event_type=event.event_type.value)
                .on_conflict_do_nothing(index_elements=[ProcessedEvent.event_id])
                .returning(ProcessedEvent.event_id)
            )
            if inserted is None:
                return False

            ticket = await session.scalar(
                select(Ticket).where(Ticket.id == ticket_id).with_for_update()
            )
            if ticket is None:
                return False

            history: list[TicketHistory] = []
            changed = False
            if not ticket.escalated:
                ticket.escalated = True
                changed = True
                history.append(
                    TicketHistory(
                        ticket_id=ticket_id,
                        event_id=event.event_id,
                        field_name="escalated",
                        old_value="false",
                        new_value="true",
                    )
                )
            if kind == "response":
                old_priority = ticket.priority
                old_index = PRIORITY_ORDER.index(old_priority)
                if old_index < len(PRIORITY_ORDER) - 1:
                    ticket.priority = PRIORITY_ORDER[old_index + 1]
                    changed = True
                    history.append(
                        TicketHistory(
                            ticket_id=ticket_id,
                            event_id=event.event_id,
                            field_name="priority",
                            old_value=old_priority.value,
                            new_value=ticket.priority.value,
                        )
                    )
            if changed:
                ticket.version += 1
            session.add_all(history)

    await cache.invalidate(ticket_id)
    return True


@asynccontextmanager
async def open_sla_consumer(
    *,
    host: str,
    port: int,
    login: str,
    password: str,
    virtualhost: str,
    queue_name: str,
    cache: TicketCache,
    timeout: float = 5.0,
) -> AsyncIterator[None]:
    """Retry consumer setup so missing queue ACLs do not stop ticket API startup."""

    async def consume_forever() -> None:
        while True:
            connection: AbstractRobustConnection | None = None
            try:
                async with asyncio.timeout(timeout):
                    connection = await connect_broker(
                        host=host,
                        port=port,
                        login=login,
                        password=password,
                        virtualhost=virtualhost,
                        timeout=timeout,
                        fail_fast="1",
                        client_properties={"connection_name": "ticket-service-sla-consumer"},
                    )
                channel = await connection.channel()
                await channel.set_qos(prefetch_count=10)
                queue = await channel.get_queue(queue_name, ensure=False)

                async def handle(message: aio_pika.IncomingMessage) -> None:
                    async with message.process(requeue=True):
                        event = EventEnvelope.from_bytes(message.body)
                        await apply_sla_breach(event, cache)

                await queue.consume(handle, no_ack=False)
                log.info("sla_consumer_started", queue=queue_name)
                # The robust connection restores the consumer after a broker reconnect.
                await asyncio.Future()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning(
                    "sla_consumer_unavailable",
                    queue=queue_name,
                    error=type(exc).__name__,
                )
            finally:
                if connection is not None:
                    await connection.close()
            await asyncio.sleep(5)

    task = asyncio.create_task(consume_forever(), name="ticket-sla-consumer")
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
