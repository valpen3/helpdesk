"""Отправка событий после COMMIT; жизненный цикл подключается отдельно."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aio_pika
from aio_pika.abc import AbstractExchange, AbstractRobustChannel, AbstractRobustConnection
from fastapi import HTTPException, Request

from helpdesk_common.events import EXCHANGE, EventEnvelope


def get_publisher(request: Request) -> "EventPublisher":
    publisher = getattr(request.app.state, "event_publisher", None)
    if publisher is None:
        raise HTTPException(status_code=503, detail="Event publishing service is not ready")
    return publisher


class EventPublishError(RuntimeError):
    """Публикация не подтверждена; событие при этом могло попасть в брокер."""


class EventPublisher:
    def __init__(
        self,
        exchange: AbstractExchange,
        *,
        timeout: float = 5.0,
        connection: AbstractRobustConnection | None = None,
        channel: AbstractRobustChannel | None = None,
    ):
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._exchange = exchange
        self._timeout = timeout
        self._connection = connection
        self._channel = channel

    async def check_ready(self) -> None:
        if (
            self._connection is None
            or self._channel is None
            or not self._connection.connected.is_set()
            or self._channel.is_closed
        ):
            raise ConnectionError("RabbitMQ publisher is not ready")
        async with asyncio.timeout(self._timeout):
            await self._channel.ready()

    async def publish(self, event: EventEnvelope) -> None:
        """Вернуть None после confirm; не повторять отправку при ошибке.

        Вызывать только после успешного COMMIT. Confirm не означает, что
        потребитель уже обработал событие. Ошибка не откатывает транзакцию БД.
        """
        message = aio_pika.Message(
            body=event.to_bytes(),
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            content_type="application/json",
            message_id=str(event.event_id),
            type=event.routing_key,
            timestamp=event.occurred_at,
            correlation_id=event.request_id,
        )
        try:
            # Also bound waiting for a robust connection to recover.
            async with asyncio.timeout(self._timeout):
                await self._exchange.publish(
                    message,
                    routing_key=event.routing_key,
                    mandatory=True,
                    timeout=self._timeout,
                )
        except Exception as exc:
            raise EventPublishError(
                f"Publication not confirmed: {event.routing_key}, event_id={event.event_id}"
            ) from exc


@asynccontextmanager
async def open_publisher(
    *,
    host: str,
    login: str,
    password: str,
    port: int = 5672,
    virtualhost: str = "/",
    timeout: float = 5.0,
) -> AsyncIterator[EventPublisher]:
    """Одно соединение на lifespan сервиса, не на каждый HTTP-запрос.

    Объекты RabbitMQ создаёт definitions.json. Здесь нет declare или миграций.
    Ошибки открытия соединения передаются вызывающему коду.
    """
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    connection: AbstractRobustConnection | None = None
    try:
        async with asyncio.timeout(timeout):
            connection = await aio_pika.connect_robust(
                host=host,
                port=port,
                login=login,
                password=password,
                virtualhost=virtualhost,
                timeout=timeout,
                fail_fast="1",
                client_properties={"connection_name": "ticket-service-publisher"},
            )
            channel = await connection.channel(publisher_confirms=True, on_return_raises=True)
            # Avoid declaring topology: service accounts will not have configure rights.
            exchange = await channel.get_exchange(EXCHANGE, ensure=False)
        yield EventPublisher(
            exchange,
            timeout=timeout,
            connection=connection,
            channel=channel,
        )
    finally:
        if connection is not None:
            await connection.close()
