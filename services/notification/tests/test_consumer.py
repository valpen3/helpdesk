import asyncio
from unittest.mock import AsyncMock

import pytest

from helpdesk_common.events import EventEnvelope, EventType
from helpdesk_notification.config import Settings
from helpdesk_notification.consumer import Consumer


def make_message(data=None, event_type=EventType.TICKET_CREATED):
    event = EventEnvelope(
        event_type=event_type,
        producer="ticket-service",
        data=data if data is not None else {
            "ticket_id": 42, "title": "Не работает VPN", "author_email": "client@example.com"
        },
    )
    message = AsyncMock()
    message.body = event.to_bytes()
    return event, message


def test_email_is_sent_before_ack():
    event, message = make_message()
    mailer = AsyncMock()

    async def send(mail):
        message.ack.assert_not_awaited()
        assert str(mail["To"]) == "client@example.com"
        assert str(mail["Subject"]) == "Обращение #42 принято"
        assert mail["X-Helpdesk-Event-ID"] == str(event.event_id)
        assert "Не работает VPN" in mail.get_content()

    mailer.send.side_effect = send
    asyncio.run(Consumer(Settings(), mailer).handle(message))
    mailer.send.assert_awaited_once()
    message.ack.assert_awaited_once()
    message.reject.assert_not_awaited()


@pytest.mark.parametrize("raw", [b"not json", make_message(data={})[1].body])
def test_bad_event_is_rejected_without_smtp(raw):
    message = AsyncMock()
    message.body = raw
    mailer = AsyncMock()
    asyncio.run(Consumer(Settings(), mailer).handle(message))
    message.reject.assert_awaited_once_with(requeue=False)
    message.ack.assert_not_awaited()
    mailer.send.assert_not_awaited()


def test_smtp_failure_requeues_without_ack():
    _, message = make_message()
    mailer = AsyncMock()
    mailer.send.side_effect = ConnectionError("SMTP unavailable")
    asyncio.run(Consumer(Settings(retry_delay=0.001), mailer).handle(message))
    message.ack.assert_not_awaited()
    message.reject.assert_awaited_once_with(requeue=True)


def test_cancellation_does_not_ack_unsent_message():
    _, message = make_message()
    mailer = AsyncMock()
    mailer.send.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(Consumer(Settings(), mailer).handle(message))
    message.ack.assert_not_awaited()
    message.reject.assert_not_awaited()


def test_other_event_types_are_explicitly_skipped_in_mvp():
    _, message = make_message(event_type=EventType.TICKET_ASSIGNED)
    mailer = AsyncMock()
    asyncio.run(Consumer(Settings(), mailer).handle(message))
    mailer.send.assert_not_awaited()
    message.ack.assert_awaited_once()
