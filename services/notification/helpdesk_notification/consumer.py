import asyncio
import hashlib

import aio_pika
from aio_pika.abc import (
    AbstractExchange,
    AbstractIncomingMessage,
    AbstractRobustChannel,
    AbstractRobustConnection,
)

from helpdesk_common import get_logger
from helpdesk_common.events import EventEnvelope
from helpdesk_notification.config import Settings
from helpdesk_notification.idempotency import DeliveryRegistry
from helpdesk_notification.mailer import Mailer, build_messages

log = get_logger(__name__)


class DeliveryInProgress(RuntimeError):
    pass


class Consumer:
    def __init__(self, settings: Settings, mailer: Mailer, registry: DeliveryRegistry):
        self.settings = settings
        self.mailer = mailer
        self.registry = registry
        self.connection: AbstractRobustConnection | None = None
        self.channel: AbstractRobustChannel | None = None
        self.retry_exchange: AbstractExchange | None = None
        self.dead_exchange: AbstractExchange | None = None
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

    async def _publish_copy(
        self,
        message: AbstractIncomingMessage,
        exchange: AbstractExchange,
        *,
        headers: dict,
    ) -> None:
        copy = aio_pika.Message(
            body=message.body,
            content_type=message.content_type or "application/json",
            content_encoding=message.content_encoding,
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            message_id=message.message_id,
            correlation_id=message.correlation_id,
            type=message.type,
            timestamp=message.timestamp,
            headers=headers,
        )
        await exchange.publish(copy, routing_key=message.routing_key or "unknown", mandatory=True)

    async def _dead_letter(self, message: AbstractIncomingMessage, reason: str) -> None:
        if self.dead_exchange is None:
            raise ConnectionError("Dead-letter exchange is not ready")
        headers = dict(message.headers or {})
        headers["x-dead-letter-reason"] = reason[:200]
        await self._publish_copy(message, self.dead_exchange, headers=headers)
        await message.ack()
        log.error(
            "notification_dead_lettered",
            message_id=message.message_id,
            routing_key=message.routing_key,
            reason=reason,
        )

    async def _retry_or_dead_letter(
        self, message: AbstractIncomingMessage, reason: str
    ) -> None:
        try:
            headers = dict(message.headers or {})
            attempts = int(headers.get("x-attempt", 0))
        except (TypeError, ValueError):
            await self._dead_letter(message, "invalid_retry_count")
            return

        if attempts < 0:
            await self._dead_letter(message, "invalid_retry_count")
            return

        if attempts >= self.settings.max_attempts:
            await self._dead_letter(message, reason)
            return
        if self.retry_exchange is None:
            raise ConnectionError("Retry exchange is not ready")

        headers["x-attempt"] = attempts + 1
        try:
            await self._publish_copy(message, self.retry_exchange, headers=headers)
        except Exception:
            log.exception("notification_retry_publish_failed", message_id=message.message_id)
            await asyncio.sleep(self.settings.retry_delay)
            await message.reject(requeue=True)
            return
        await message.ack()
        log.warning(
            "notification_retry_scheduled",
            message_id=message.message_id,
            attempt=attempts + 1,
            reason=reason,
        )

    async def handle(self, message: AbstractIncomingMessage) -> None:
        try:
            event = EventEnvelope.from_bytes(message.body)
            notifications = build_messages(event, self.settings)
        except ValueError as exc:
            await self._dead_letter(message, f"invalid_event:{type(exc).__name__}")
            return

        try:
            for recipient, mail in notifications:
                state = await self.registry.claim(str(event.event_id), recipient)
                if state is None:
                    log.info(
                        "notification_duplicate_skipped",
                        event_id=str(event.event_id),
                        recipient_ref=hashlib.sha256(
                            recipient.casefold().encode()
                        ).hexdigest()[:16],
                    )
                    continue
                if not state:
                    raise DeliveryInProgress("another worker holds the delivery lock")
                try:
                    await self.mailer.send(mail)
                    await self.registry.mark_sent(str(event.event_id), recipient)
                except asyncio.CancelledError:
                    try:
                        await self.registry.release(str(event.event_id), recipient)
                    except Exception:
                        log.warning(
                            "notification_lock_release_failed",
                            event_id=str(event.event_id),
                        )
                    raise
                except Exception:
                    await self.registry.release(str(event.event_id), recipient)
                    raise
                log.info(
                    "notification_sent",
                    event_id=str(event.event_id),
                    event_type=event.routing_key,
                    request_id=event.request_id,
                    recipient_ref=hashlib.sha256(
                        recipient.casefold().encode()
                    ).hexdigest()[:16],
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._retry_or_dead_letter(message, type(exc).__name__)
            return

        await message.ack()

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
                        channel = await connection.channel(
                            publisher_confirms=True, on_return_raises=True
                        )
                        await channel.set_qos(prefetch_count=10)
                        queue = await channel.declare_queue(
                            self.settings.mq_queue, passive=True, robust=True
                        )
                        self.retry_exchange = await channel.get_exchange(
                            self.settings.retry_exchange, ensure=False
                        )
                        self.dead_exchange = await channel.get_exchange(
                            self.settings.dead_exchange, ensure=False
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
                self.retry_exchange = None
                self.dead_exchange = None
            await asyncio.sleep(self.settings.retry_delay)


__all__ = ["Consumer", "DeliveryInProgress"]
