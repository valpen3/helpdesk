"""Builders for the ticket-service event contract."""

from app.models import Comment, Ticket
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


def ticket_updated_event(
    ticket: Ticket, changed_fields: list[str], request_id: str | None
) -> EventEnvelope:
    data = {
        "ticket_id": ticket.id,
        "title": ticket.title,
        "changed_fields": changed_fields,
        "author_email": ticket.author_email,
    }
    if "priority" in changed_fields:
        data["priority"] = ticket.priority.value
    return EventEnvelope(
        event_type=EventType.TICKET_UPDATED,
        producer="ticket-service",
        request_id=request_id,
        data=data,
    )


def ticket_status_changed_event(
    ticket: Ticket,
    old_status: str,
    actor_role: str,
    request_id: str | None,
) -> EventEnvelope:
    return EventEnvelope(
        event_type=EventType.TICKET_STATUS_CHANGED,
        producer="ticket-service",
        request_id=request_id,
        data={
            "ticket_id": ticket.id,
            "title": ticket.title,
            "old_status": old_status,
            "new_status": ticket.status.value,
            "author_email": ticket.author_email,
            "assignee_email": ticket.assignee_email,
            "actor_role": actor_role,
        },
    )


def ticket_assigned_event(ticket: Ticket, request_id: str | None) -> EventEnvelope:
    return EventEnvelope(
        event_type=EventType.TICKET_ASSIGNED,
        producer="ticket-service",
        request_id=request_id,
        data={
            "ticket_id": ticket.id,
            "title": ticket.title,
            "assignee_id": str(ticket.assignee_id),
            "assignee_email": ticket.assignee_email,
            "author_email": ticket.author_email,
        },
    )


def ticket_commented_event(
    ticket: Ticket, comment: Comment, actor_role: str, request_id: str | None
) -> EventEnvelope:
    return EventEnvelope(
        event_type=EventType.TICKET_COMMENTED,
        producer="ticket-service",
        request_id=request_id,
        data={
            "ticket_id": ticket.id,
            "title": ticket.title,
            "comment_id": comment.id,
            "is_internal": comment.is_internal,
            "actor_role": actor_role,
            "author_email": ticket.author_email,
            "assignee_email": ticket.assignee_email,
        },
    )


def ticket_deleted_event(ticket_id: int, request_id: str | None) -> EventEnvelope:
    return EventEnvelope(
        event_type=EventType.TICKET_DELETED,
        producer="ticket-service",
        request_id=request_id,
        data={"ticket_id": ticket_id},
    )
