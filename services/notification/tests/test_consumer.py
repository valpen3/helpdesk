import asyncio
from unittest.mock import AsyncMock

import pytest

from helpdesk_common.events import EventEnvelope, EventType
from helpdesk_notification.config import Settings
from helpdesk_notification.consumer import Consumer
from helpdesk_notification.mailer import Mailer, build_messages


def make_event(event_type=EventType.TICKET_CREATED, data=None):
    if data is None:
        data = {
            "ticket_id": 42,
            "title": "VPN не подключается",
            "priority": "medium",
            "author_email": "client@example.com",
        }
    return EventEnvelope(event_type=event_type, producer="ticket-service", data=data)


def make_message(event=None, *, headers=None):
    event = event or make_event()
    message = AsyncMock()
    message.body = event.to_bytes()
    message.headers = headers or {}
    message.routing_key = event.routing_key
    message.message_id = str(event.event_id)
    message.content_type = "application/json"
    message.content_encoding = None
    message.correlation_id = None
    message.type = event.routing_key
    message.timestamp = event.occurred_at
    return event, message


def build_consumer(settings=None):
    registry = AsyncMock()
    registry.claim.return_value = True
    return Consumer(settings or Settings(), AsyncMock(spec=Mailer), registry), registry


def test_ticket_created_critical_notifies_customer_and_oncall():
    event = make_event(data={
        "ticket_id": 42,
        "title": "VPN недоступен у отдела",
        "priority": "critical",
        "author_email": "client@example.com",
    })

    messages = build_messages(event, Settings(oncall_email="oncall@example.com"))

    assert {recipient for recipient, _ in messages} == {
        "client@example.com",
        "oncall@example.com",
    }
    assert all("Критичная заявка #42" in str(message["Subject"]) for _, message in messages)


def test_assigned_event_notifies_customer_and_agent_once_each():
    event = make_event(
        EventType.TICKET_ASSIGNED,
        {
            "ticket_id": 42,
            "title": "VPN не подключается",
            "author_email": "CLIENT@example.com",
            "assignee_email": "agent@example.com",
        },
    )

    messages = build_messages(event, Settings())

    assert [recipient for recipient, _ in messages] == [
        "CLIENT@example.com",
        "agent@example.com",
    ]


def test_internal_comment_does_not_send_email():
    event = make_event(
        EventType.TICKET_COMMENTED,
        {
            "ticket_id": 42,
            "title": "VPN не подключается",
            "author_email": "client@example.com",
            "assignee_email": "agent@example.com",
            "actor_role": "agent",
            "is_internal": True,
        },
    )

    assert build_messages(event, Settings()) == []


def test_sla_breach_notifies_admin_and_current_assignee():
    event = make_event(
        EventType.TICKET_SLA_BREACHED,
        {
            "ticket_id": 42,
            "title": "VPN не подключается",
            "kind": "response",
            "due_at": "2026-11-15T12:00:00+00:00",
            "priority": "high",
            "assignee_email": "agent@example.com",
        },
    )

    messages = build_messages(event, Settings(admin_notify_email="admin@example.com"))

    assert {recipient for recipient, _ in messages} == {
        "admin@example.com",
        "agent@example.com",
    }


def test_email_is_sent_before_ack_and_marked_done():
    event, message = make_message()
    consumer, registry = build_consumer()

    async def send(mail):
        message.ack.assert_not_awaited()
        assert str(mail["To"]) == "client@example.com"
        assert "Заявка #42 принята" in str(mail["Subject"])
        assert mail["X-Helpdesk-Event-ID"] == str(event.event_id)
        assert "VPN не подключается" in mail.get_content()

    consumer.mailer.send.side_effect = send
    asyncio.run(consumer.handle(message))

    consumer.mailer.send.assert_awaited_once()
    registry.claim.assert_awaited_once_with(str(event.event_id), "client@example.com")
    registry.mark_sent.assert_awaited_once_with(str(event.event_id), "client@example.com")
    message.ack.assert_awaited_once()
    message.reject.assert_not_awaited()


def test_duplicate_successful_delivery_is_skipped():
    event, message = make_message()
    consumer, registry = build_consumer()
    registry.claim.return_value = None

    asyncio.run(consumer.handle(message))

    consumer.mailer.send.assert_not_awaited()
    message.ack.assert_awaited_once()


def test_invalid_event_is_sent_to_dead_letter_queue():
    _, message = make_message()
    message.body = b"not json"
    consumer, _ = build_consumer()
    consumer.dead_exchange = AsyncMock()

    asyncio.run(consumer.handle(message))

    consumer.dead_exchange.publish.assert_awaited_once()
    message.ack.assert_awaited_once()
    message.reject.assert_not_awaited()


def test_smtp_failure_is_published_to_delayed_retry_queue():
    _, message = make_message()
    consumer, registry = build_consumer(Settings(retry_delay=0.001))
    consumer.retry_exchange = AsyncMock()
    consumer.mailer.send.side_effect = ConnectionError("SMTP unavailable")

    asyncio.run(consumer.handle(message))

    registry.release.assert_awaited_once()
    consumer.retry_exchange.publish.assert_awaited_once()
    retry_message = consumer.retry_exchange.publish.await_args.args[0]
    assert retry_message.headers["x-attempt"] == 1
    message.ack.assert_awaited_once()
    message.reject.assert_not_awaited()


def test_retry_limit_sends_message_to_dead_letter_queue():
    _, message = make_message(headers={"x-attempt": 3})
    consumer, _ = build_consumer()
    consumer.dead_exchange = AsyncMock()
    consumer.mailer.send.side_effect = ConnectionError("SMTP unavailable")

    asyncio.run(consumer.handle(message))

    consumer.dead_exchange.publish.assert_awaited_once()
    message.ack.assert_awaited_once()
    message.reject.assert_not_awaited()


def test_cancellation_does_not_ack_unsent_message():
    _, message = make_message()
    consumer, registry = build_consumer()
    consumer.mailer.send.side_effect = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(consumer.handle(message))

    message.ack.assert_not_awaited()
    message.reject.assert_not_awaited()
    registry.release.assert_awaited_once()
