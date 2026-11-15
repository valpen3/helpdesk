import asyncio

from aio_pika.abc import (
    AbstractIncomingMessage,
    AbstractRobustChannel,
    AbstractRobustConnection,
)
from sqlalchemy import select

from helpdesk_common import get_logger
from helpdesk_common.broker import connect_broker
from helpdesk_common.events import EventEnvelope, EventType
from helpdesk_sla.config import Settings
from helpdesk_sla.db import session_factory
from helpdesk_sla.domain import (
    apply_ticket_event,
    create_sla_record,
    policies_by_priority,
    required_int,
)
from helpdesk_sla.models import ProcessedEvent, SlaPolicy, SlaRecord

log = get_logger(__name__)

SLA_INPUT_EVENTS = {
    EventType.TICKET_CREATED,
    EventType.TICKET_UPDATED,
    EventType.TICKET_STATUS_CHANGED,
    EventType.TICKET_ASSIGNED,
    EventType.TICKET_DELETED,
}


class SlaConsumer:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.connection: AbstractRobustConnection | None = None
        self.channel: AbstractRobustChannel | None = None
        self.consuming = False

    async def check_ready(self) -> None:
        if (
            not self.consuming
            or self.connection is None
            or self.channel is None
            or not self.connection.connected.is_set()
            or self.channel.is_closed
        ):
            raise ConnectionError("SLA RabbitMQ consumer is not ready")
        await self.channel.ready()

    async def process(self, event: EventEnvelope) -> bool:
        if event.event_type not in SLA_INPUT_EVENTS:
            return False
        ticket_id = required_int(event.data, "ticket_id")
        async with session_factory() as session:
            async with session.begin():
                if await session.get(ProcessedEvent, event.event_id) is not None:
                    return True

                policy_rows = await session.scalars(select(SlaPolicy))
                policies = policies_by_priority(list(policy_rows))
                if event.event_type == EventType.TICKET_CREATED:
                    record = await session.get(SlaRecord, ticket_id, with_for_update=True)
                    if record is None:
                        record = create_sla_record(event, policies, self.settings.sla_time_scale)
                        session.add(record)
                else:
                    record = await session.get(SlaRecord, ticket_id, with_for_update=True)
                    if record is not None:
                        apply_ticket_event(record, event, policies, self.settings.sla_time_scale)
                    else:
                        log.warning(
                            "sla_event_without_ticket",
                            event_id=str(event.event_id),
                            event_type=event.routing_key,
                            ticket_id=ticket_id,
                        )
                session.add(ProcessedEvent(event_id=event.event_id))
        return True

    async def handle(self, message: AbstractIncomingMessage) -> None:
        try:
            event = EventEnvelope.from_bytes(message.body)
            handled = await self.process(event)
        except ValueError as exc:
            log.warning("invalid_sla_event", reason=type(exc).__name__)
            await message.reject(requeue=False)
            return
        except Exception as exc:
            log.warning(
                "sla_event_processing_failed",
                reason=type(exc).__name__,
                message_id=message.message_id,
            )
            await asyncio.sleep(self.settings.retry_delay)
            await message.reject(requeue=True)
            return

        await message.ack()
        if handled:
            log.info(
                "sla_event_processed",
                event_id=str(event.event_id),
                event_type=event.routing_key,
            )

    async def run(self) -> None:
        while True:
            try:
                async with asyncio.timeout(self.settings.mq_timeout):
                    connection = await connect_broker(
                        host=self.settings.mq_host,
                        port=self.settings.mq_port,
                        login=self.settings.mq_user,
                        password=self.settings.mq_password.get_secret_value(),
                        virtualhost=self.settings.mq_vhost,
                        timeout=self.settings.mq_timeout,
                        fail_fast="1",
                    )
                async with connection:
                    self.connection = connection
                    async with asyncio.timeout(self.settings.mq_timeout):
                        channel = await connection.channel()
                        await channel.set_qos(prefetch_count=1)
                        queue = await channel.declare_queue(
                            self.settings.mq_queue, passive=True, robust=True
                        )
                        self.channel = channel
                    async with queue.iterator() as messages:
                        self.consuming = True
                        log.info("sla_consumer_started", queue=self.settings.mq_queue)
                        async for message in messages:
                            await self.handle(message)
            except Exception as exc:
                log.warning("sla_consumer_disconnected", reason=type(exc).__name__)
            finally:
                self.consuming = False
                self.connection = None
                self.channel = None
            await asyncio.sleep(self.settings.retry_delay)
