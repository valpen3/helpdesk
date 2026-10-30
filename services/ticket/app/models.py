"""Таблицы сервиса тикетов. Схему создаёт Alembic, а не приложение."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TicketStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    WAITING_CLIENT = "waiting_client"
    RESOLVED = "resolved"
    CLOSED = "closed"


class TicketPriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


def enum_values(enum_class: type[StrEnum]) -> list[str]:
    return [item.value for item in enum_class]


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[TicketStatus] = mapped_column(
        Enum(TicketStatus, name="ticket_status", values_callable=enum_values),
        server_default="open",
        index=True,
    )
    priority: Mapped[TicketPriority] = mapped_column(
        Enum(TicketPriority, name="ticket_priority", values_callable=enum_values),
        server_default="medium",
    )
    author_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    author_email: Mapped[str] = mapped_column(String(320))
    assignee_id: Mapped[UUID | None] = mapped_column(Uuid, index=True)
    assignee_email: Mapped[str | None] = mapped_column(String(320))
    escalated: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Comment(Base):
    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tickets.id", ondelete="CASCADE"), index=True
    )
    author_id: Mapped[UUID] = mapped_column(Uuid)
    author_email: Mapped[str] = mapped_column(String(320))
    author_role: Mapped[str] = mapped_column(String(20))
    body: Mapped[str] = mapped_column(Text)
    is_internal: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
