import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aio_pika
from aio_pika.abc import AbstractExchange, AbstractRobustConnection

from helpdesk_common.events import EventEnvelope, EXCHANGE
from helpdesk_sla.config import Settings


class SlaPublisher:
    def __init__(self, exchange: AbstractExchange, timeout: float = 5.0):
        self.exchange = exchange
        self.timeout = timeout

    async def publish(self, event: EventEnvelope) -> None:
        message = aio_pika.Message(
            body=event.to_bytes(),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            message_id=str(event.event_id),
            type=event.routing_key,
            timestamp=event.occurred_at,
        )
        try:
            async with asyncio.timeout(self.timeout):
                await self.exchange.publish(message, routing_key=event.routing_key, mandatory=True)
        except Exception as exc:
            raise RuntimeError(f"Could not publish {event.routing_key}") from exc


@asynccontextmanager
async def open_publisher(settings: Settings) -> AsyncIterator[SlaPublisher]:
    connection: AbstractRobustConnection = await aio_pika.connect_robust(
        host=settings.mq_host,
        port=settings.mq_port,
        login=settings.mq_user,
        password=settings.mq_password.get_secret_value(),
        virtualhost=settings.mq_vhost,
        timeout=settings.mq_timeout,
        fail_fast="1",
    )
    try:
        channel = await connection.channel(publisher_confirms=True, on_return_raises=True)
        exchange = await channel.get_exchange(EXCHANGE, ensure=False)
        yield SlaPublisher(exchange, settings.mq_timeout)
    finally:
        await connection.close()
