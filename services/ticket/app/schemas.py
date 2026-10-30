"""Правила для входящих запросов и JSON-ответов API тикетов."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints

from app.models import TicketPriority, TicketStatus

MAX_TICKET_ID = 2**63 - 1
TicketId = Annotated[int, Field(gt=0, le=MAX_TICKET_ID)]


class TicketCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    description: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    priority: TicketPriority = TicketPriority.MEDIUM


class TicketStatusChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: TicketStatus
    version: Annotated[int, Field(strict=True, ge=1, le=2**31 - 2)]


class TicketResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str
    status: TicketStatus
    priority: TicketPriority
    author_id: UUID
    author_email: EmailStr
    assignee_id: UUID | None
    assignee_email: str | None
    escalated: bool
    version: int
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None
    closed_at: datetime | None


class TicketListResponse(BaseModel):
    items: list[TicketResponse]
    total: int
    page: int
    size: int
