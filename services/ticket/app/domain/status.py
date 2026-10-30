"""Допустимые переходы статусов; проверки ролей появятся вместе с JWT."""

from app.models import TicketStatus

ALLOWED_TRANSITIONS: dict[TicketStatus, frozenset[TicketStatus]] = {
    TicketStatus.OPEN: frozenset({TicketStatus.IN_PROGRESS, TicketStatus.CLOSED}),
    TicketStatus.IN_PROGRESS: frozenset({TicketStatus.WAITING_CLIENT, TicketStatus.RESOLVED}),
    TicketStatus.WAITING_CLIENT: frozenset({TicketStatus.IN_PROGRESS}),
    TicketStatus.RESOLVED: frozenset({TicketStatus.CLOSED, TicketStatus.IN_PROGRESS}),
    TicketStatus.CLOSED: frozenset(),
}
