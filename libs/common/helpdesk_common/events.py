"""Единый формат событий между сервисами (контракт описан в docs/api.md).

Тип события совпадает с routing key в exchange helpdesk.events.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

EXCHANGE = "helpdesk.events"


class EventType(StrEnum):
    TICKET_CREATED = "ticket.created"
    TICKET_UPDATED = "ticket.updated"
    TICKET_STATUS_CHANGED = "ticket.status_changed"
    TICKET_ASSIGNED = "ticket.assigned"
    TICKET_COMMENTED = "ticket.commented"
    TICKET_DELETED = "ticket.deleted"
    TICKET_SLA_BREACHED = "ticket.sla_breached"


class EventEnvelope(BaseModel):
    """Конверт события. Поля data зависят от event_type (см. docs/api.md)."""

    # Неизвестные поля игнорируем: так продюсер может добавлять новые поля,
    # не ломая потребителей, которые ещё о них не знают.
    model_config = ConfigDict(extra="ignore")

    event_id: UUID = Field(default_factory=uuid4)
    event_type: EventType
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    producer: str
    request_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)

    @property
    def routing_key(self) -> str:
        return self.event_type.value

    def to_bytes(self) -> bytes:
        return self.model_dump_json().encode()

    @classmethod
    def from_bytes(cls, raw: bytes) -> "EventEnvelope":
        return cls.model_validate_json(raw)
