"""Builders for the ticket-service event contract."""

from app.models import Ticket
from helpdesk_common.events import EventEnvelope, EventType


def ticket_created_event(ticket: Ticket, request_id: str | None) -> EventEnvelope:
    return EventEnvelope(
        event_type=EventType.TICKET_CREATED,
        producer="ticket-service",
        request_id=request_id,
        data={
            "ticket_id": ticket.id,
            "title": ticket.title,
            "priority": ticket.priority.value,
            "author_id": str(ticket.author_id),
            "author_email": ticket.author_email,
            "created_at": ticket.created_at.isoformat(),
        },
    )
