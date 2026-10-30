"""Четыре эндпоинта этапа 2: создание, чтение, список и смена статуса."""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import EmailStr
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.domain.status import ALLOWED_TRANSITIONS
from app.models import Ticket, TicketPriority, TicketStatus
from app.schemas import (
    TicketCreate,
    TicketId,
    TicketListResponse,
    TicketResponse,
    TicketStatusChange,
)

router = APIRouter(prefix="/api/v1/tickets", tags=["tickets"])
Session = Annotated[AsyncSession, Depends(get_session)]


@router.post("", response_model=TicketResponse, status_code=201)
async def create_ticket(
    payload: TicketCreate,
    session: Session,
    x_user_id: Annotated[UUID, Header()],
    x_user_email: Annotated[EmailStr, Header(max_length=320)],
) -> TicketResponse:
    ticket = Ticket(**payload.model_dump(), author_id=x_user_id, author_email=str(x_user_email))
    session.add(ticket)
    await session.commit()
    await session.refresh(ticket)
    return TicketResponse.model_validate(ticket)


@router.get("", response_model=TicketListResponse)
async def list_tickets(
    session: Session,
    page: Annotated[int, Query(ge=1, le=2**31 - 1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    status: TicketStatus | None = None,
    priority: TicketPriority | None = None,
    author_id: UUID | None = None,
    assignee_id: UUID | None = None,
    escalated: bool | None = None,
) -> TicketListResponse:
    filters = []
    for column, value in (
        (Ticket.status, status),
        (Ticket.priority, priority),
        (Ticket.author_id, author_id),
        (Ticket.assignee_id, assignee_id),
        (Ticket.escalated, escalated),
    ):
        if value is not None:
            filters.append(column == value)
    total = await session.scalar(select(func.count()).select_from(Ticket).where(*filters))
    tickets = await session.scalars(
        select(Ticket)
        .where(*filters)
        .order_by(Ticket.created_at.desc(), Ticket.id.desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    return TicketListResponse(
        items=[TicketResponse.model_validate(ticket) for ticket in tickets],
        total=total or 0,
        page=page,
        size=size,
    )


@router.get("/{ticket_id}", response_model=TicketResponse)
async def get_ticket(ticket_id: TicketId, session: Session) -> TicketResponse:
    ticket = await session.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Тикет не найден")
    return TicketResponse.model_validate(ticket)


@router.post("/{ticket_id}/status", response_model=TicketResponse)
async def change_ticket_status(
    ticket_id: TicketId, payload: TicketStatusChange, session: Session
) -> TicketResponse:
    ticket = await session.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Тикет не найден")
    if ticket.version != payload.version:
        raise HTTPException(status_code=409, detail="Тикет уже изменён; перечитайте его")
    if payload.status not in ALLOWED_TRANSITIONS[ticket.status]:
        raise HTTPException(
            status_code=422,
            detail=f"переход {ticket.status} → {payload.status} запрещён",
        )

    now = datetime.now(UTC)
    values = {"status": payload.status, "version": Ticket.version + 1, "updated_at": now}
    if payload.status == TicketStatus.RESOLVED:
        values["resolved_at"] = now
    elif payload.status == TicketStatus.CLOSED:
        values["closed_at"] = now
    elif payload.status == TicketStatus.IN_PROGRESS:
        values.update(resolved_at=None, closed_at=None)

    # Проверка версии выполняется самой БД, в том же UPDATE, что и изменение.
    # Два одновременных запроса не смогут оба обновить одну версию тикета.
    changed = await session.scalar(
        update(Ticket)
        .where(Ticket.id == ticket_id, Ticket.version == payload.version)
        .values(**values)
        .returning(Ticket)
        .execution_options(populate_existing=True, synchronize_session=False)
    )
    if changed is None:
        raise HTTPException(status_code=409, detail="Тикет уже изменён; перечитайте его")
    await session.commit()
    return TicketResponse.model_validate(changed)
