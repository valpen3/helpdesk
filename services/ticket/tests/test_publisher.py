import asyncio
from unittest.mock import AsyncMock, patch

import aio_pika
import pytest

from app.messaging.publisher import EventPublisher, EventPublishError, open_publisher
from helpdesk_common.events import EXCHANGE, EventEnvelope, EventType


def make_event():
    return EventEnvelope(
        event_type=EventType.TICKET_CREATED,
        producer="ticket-service",
        request_id="request-42",
        data={"ticket_id": 42, "title": "Не работает VPN"},
    )


def test_persistent_message_preserves_event_and_correlation():
    event = make_event()
    exchange = AsyncMock()
    asyncio.run(EventPublisher(exchange).publish(event))

    message = exchange.publish.call_args.args[0]
    assert EventEnvelope.from_bytes(message.body) == event
    assert message.delivery_mode == aio_pika.DeliveryMode.PERSISTENT
    assert message.content_type == "application/json"
    assert message.message_id == str(event.event_id)
    assert message.correlation_id == event.request_id
    assert exchange.publish.call_args.kwargs["routing_key"] == event.routing_key
    assert exchange.publish.call_args.kwargs["mandatory"] is True


def test_failed_publish_is_reported_without_retry():
    cause = ConnectionError("broker unavailable")
    exchange = AsyncMock()
    exchange.publish.side_effect = cause
    event = make_event()
    with pytest.raises(EventPublishError, match=str(event.event_id)) as error:
        asyncio.run(EventPublisher(exchange).publish(event))
    assert error.value.__cause__ is cause
    assert exchange.publish.await_count == 1


def test_publish_timeout_bounds_wait_for_reconnection():
    async def never_returns(*args, **kwargs):
        await asyncio.Event().wait()

    exchange = AsyncMock()
    exchange.publish.side_effect = never_returns
    with pytest.raises(EventPublishError) as error:
        asyncio.run(EventPublisher(exchange, timeout=0.01).publish(make_event()))
    assert isinstance(error.value.__cause__, TimeoutError)


def test_connection_uses_confirms_and_closes_after_caller_error():
    connection = AsyncMock()
    channel = connection.channel.return_value

    async def scenario():
        async with open_publisher(host="rabbitmq", login="user", password="test"):
            raise ValueError("caller failed")

    with patch("app.messaging.publisher.aio_pika.connect_robust", return_value=connection):
        with pytest.raises(ValueError, match="caller failed"):
            asyncio.run(scenario())
    connection.channel.assert_awaited_once_with(publisher_confirms=True, on_return_raises=True)
    channel.get_exchange.assert_awaited_once_with(EXCHANGE, ensure=False)
    channel.declare_exchange.assert_not_called()
    connection.close.assert_awaited_once()


def test_connection_is_closed_when_channel_setup_fails():
    connection = AsyncMock()
    connection.channel.side_effect = ConnectionError("channel failed")

    async def scenario():
        async with open_publisher(host="rabbitmq", login="user", password="test"):
            pytest.fail("failed setup must not yield a publisher")

    with patch("app.messaging.publisher.aio_pika.connect_robust", return_value=connection):
        with pytest.raises(ConnectionError, match="channel failed"):
            asyncio.run(scenario())
    connection.close.assert_awaited_once()
