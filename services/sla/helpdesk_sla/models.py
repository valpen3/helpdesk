from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, Integer, String, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SlaPolicy(Base):
    __tablename__ = "sla_policies"
    __table_args__ = (
        CheckConstraint("response_minutes > 0", name="ck_sla_policy_response_positive"),
        CheckConstraint("resolution_minutes > 0", name="ck_sla_policy_resolution_positive"),
    )

    priority: Mapped[str] = mapped_column(String(20), primary_key=True)
    response_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    resolution_minutes: Mapped[int] = mapped_column(Integer, nullable=False)


class SlaRecord(Base):
    __tablename__ = "sla_records"
    __table_args__ = (
        CheckConstraint(
            "priority IN ('low', 'medium', 'high', 'critical')",
            name="ck_sla_record_priority",
        ),
    )

    ticket_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    priority: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    response_due: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolution_due: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paused_total_sec: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    response_breached: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    resolution_breached: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    closed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))


class ProcessedEvent(Base):
    __tablename__ = "processed_events"

    event_id: Mapped[UUID] = mapped_column(primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
