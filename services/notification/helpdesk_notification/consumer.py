import asyncio

import aio_pika
from aio_pika.abc import AbstractIncomingMessage, AbstractRobustChannel, AbstractRobustConnection

from helpdesk_common import get_logger
from helpdesk_common.events import EventEnvelope, EventType
from helpdesk_notification.config import Settings
from helpdesk_notification.mailer import Mailer, build_message

log = get_logger(__name__)


class Consumer:
    def __init__(self, settings: Settings, mailer: Mailer):
        self.settings = settings
        self.mailer = mailer
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
            raise ConnectionError("RabbitMQ consumer is not ready")
        await self.channel.ready()

    async def handle(self, message: AbstractIncomingMessage) -> None:
        try:
            event = EventEnvelope.from_bytes(message.body)
            log.info("event_received", event_id=str(event.event_id), event_type=event.routing_key)
            if event.event_type != EventType.TICKET_CREATED:
                log.warning("notification_not_implemented", event_type=event.routing_key)
                await message.ack()
                return
            mail = build_message(event, self.settings.mail_from)
        except ValueError as exc:
            log.warning("invalid_notification_event", reason=type(exc).__name__)
            await message.reject(requeue=False)
            return

        try:
            await self.mailer.send(mail)
        except Exception as exc:
            log.warning("smtp_send_failed", event_id=str(event.event_id), reason=type(exc).__name__)
            # MVP backoff. Dedicated retry queues and DLQ are a later stage.
            await asyncio.sleep(self.settings.retry_delay)
            await message.reject(requeue=True)
            return
        await message.ack()
        log.info("notification_sent", event_id=str(event.event_id), request_id=event.request_id)

    async def run(self) -> None:
        while True:
            try:
                async with asyncio.timeout(self.settings.io_timeout):
                    connection = await aio_pika.connect_robust(
                        host=self.settings.mq_host,
                        port=self.settings.mq_port,
                        login=self.settings.mq_user,
                        password=self.settings.mq_password.get_secret_value(),
                        virtualhost=self.settings.mq_vhost,
                        timeout=self.settings.io_timeout,
                        fail_fast="1",
                    )
                async with connection:
                    self.connection = connection
                    async with asyncio.timeout(self.settings.io_timeout):
                        channel = await connection.channel()
                        await channel.set_qos(prefetch_count=10)
                        # Passive lookup does not create a queue. Register it with
                        # RobustChannel so its consumer is restored after reconnect.
                        queue = await channel.declare_queue(
                            self.settings.mq_queue, passive=True, robust=True
                        )
                        self.channel = channel
                    async with queue.iterator() as messages:
                        self.consuming = True
                        log.info("consumer_started", queue=self.settings.mq_queue)
                        async for message in messages:
                            await self.handle(message)
            except Exception as exc:
                log.warning("consumer_disconnected", reason=type(exc).__name__)
            finally:
                self.consuming = False
                self.connection = None
                self.channel = None
            await asyncio.sleep(self.settings.retry_delay)
