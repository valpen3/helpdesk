from helpdesk_common.events import EventEnvelope, EventType


def test_roundtrip():
    event = EventEnvelope(
        event_type=EventType.TICKET_CREATED,
        producer="ticket-service",
        data={"ticket_id": 42, "title": "Не работает VPN"},
    )
    restored = EventEnvelope.from_bytes(event.to_bytes())
    assert restored == event
    assert restored.routing_key == "ticket.created"


def test_unknown_fields_are_ignored():
    raw = (
        b'{"event_id": "7f1c2b9e-1d2a-4c55-9a7e-0b8f3c6d5e4a", "event_type": "ticket.deleted",'
        b' "occurred_at": "2026-11-01T10:00:00Z", "producer": "ticket-service",'
        b' "data": {"ticket_id": 1}, "new_field_from_future": true}'
    )
    event = EventEnvelope.from_bytes(raw)
    assert event.event_type is EventType.TICKET_DELETED
