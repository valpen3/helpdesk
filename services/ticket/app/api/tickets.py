"""Четыре эндпоинта этапа 2: создание, чтение, список и смена статуса."""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from pydantic import EmailStr
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import TicketCache, get_cache
from app.db import get_session
from app.domain.status import ALLOWED_TRANSITIONS
from app.messaging.events import (
    ticket_assigned_event,
    ticket_commented_event,
    ticket_created_event,
    ticket_deleted_event,
    ticket_status_changed_event,
    ticket_updated_event,
)
from app.messaging.outbox import add_to_outbox
from app.models import Comment, Ticket, TicketPriority, TicketStatus
from app.schemas import (
    CommentCreate,
    CommentResponse,
    TicketAssignment,
    TicketCreate,
    TicketId,
    TicketListResponse,
    TicketResponse,
    TicketStatusChange,
    TicketUpdate,
)

router = APIRouter(prefix="/api/v1/tickets", tags=["tickets"])
Session = Annotated[AsyncSession, Depends(get_session)]
Cache = Annotated[TicketCache, Depends(get_cache)]
@router.post("", response_model=TicketResponse, status_code=201)
async def create_ticket(
    payload: TicketCreate,
    session: Session,
    cache: Cache,
    x_user_id: Annotated[UUID, Header()],
    x_user_email: Annotated[EmailStr, Header(max_length=320)],
    request: Request,
) -> TicketResponse:
    ticket = Ticket(**payload.model_dump(), author_id=x_user_id, author_email=str(x_user_email))
    session.add(ticket)
    await session.flush()
    add_to_outbox(
        session, ticket_created_event(ticket, getattr(request.state, "request_id", None))
    )
    await session.commit()
    await cache.invalidate(ticket.id)
    await session.refresh(ticket)
    return TicketResponse.model_validate(ticket)


@router.get("", response_model=TicketListResponse)
async def list_tickets(
    session: Session,
    cache: Cache,
    response: Response,
    page: Annotated[int, Query(ge=1, le=2**31 - 1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    status: TicketStatus | None = None,
    priority: TicketPriority | None = None,
    author_id: UUID | None = None,
    assignee_id: UUID | None = None,
    escalated: bool | None = None,
) -> TicketListResponse:
    params = dict(
        page=page,
        size=size,
        status=status,
        priority=priority,
        author_id=author_id,
        assignee_id=assignee_id,
        escalated=escalated,
    )
    cached, token = await cache.read_list(params)
    response.headers["X-Cache"] = "HIT" if cached is not None else "MISS"
    if cached is not None:
        return cached
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
    result = TicketListResponse(
        items=[TicketResponse.model_validate(ticket) for ticket in tickets],
        total=total or 0,
        page=page,
        size=size,
    )
    await cache.store_list(params, result, token)
    return result


@router.get("/{ticket_id}", response_model=TicketResponse)
async def get_ticket(
    ticket_id: TicketId, session: Session, cache: Cache, response: Response
) -> TicketResponse:
    cached, token = await cache.read_ticket(ticket_id)
    response.headers["X-Cache"] = "HIT" if cached is not None else "MISS"
    if cached is not None:
        return cached
    ticket = await session.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Тикет не найден", headers={"X-Cache": "MISS"})
    result = TicketResponse.model_validate(ticket)
    await cache.store_ticket(result, token)
    return result


@router.post("/{ticket_id}/status", response_model=TicketResponse)
async def change_ticket_status(
    ticket_id: TicketId,
    payload: TicketStatusChange,
    session: Session,
    cache: Cache,
    request: Request,
    actor_role: Annotated[str, Header(alias="X-User-Role", max_length=20)] = "client",
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

    old_status = ticket.status.value
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
    add_to_outbox(
        session,
        ticket_status_changed_event(
            changed, old_status, actor_role, getattr(request.state, "request_id", None)
        ),
    )
    await session.commit()
    await cache.invalidate(ticket_id)
    return TicketResponse.model_validate(changed)


@router.patch("/{ticket_id}", response_model=TicketResponse)
async def update_ticket(
    ticket_id: TicketId,
    payload: TicketUpdate,
    session: Session,
    cache: Cache,
    request: Request,
) -> TicketResponse:
    changed_fields = sorted(payload.model_fields_set - {"version"})
    if not changed_fields:
        raise HTTPException(status_code=422, detail="At least one ticket field must be provided")
    if any(getattr(payload, name) is None for name in changed_fields):
        raise HTTPException(status_code=422, detail="Updated fields cannot be null")
    ticket = await session.scalar(
        update(Ticket)
        .where(Ticket.id == ticket_id, Ticket.version == payload.version)
        .values(
            **{name: getattr(payload, name) for name in changed_fields},
            version=Ticket.version + 1,
            updated_at=datetime.now(UTC),
        )
        .returning(Ticket)
        .execution_options(populate_existing=True, synchronize_session=False)
    )
    if ticket is None:
        exists = await session.get(Ticket, ticket_id)
        if exists is None:
            raise HTTPException(status_code=404, detail="Ticket not found")
        raise HTTPException(status_code=409, detail="Ticket changed; reload it and retry")
    add_to_outbox(
        session,
        ticket_updated_event(ticket, changed_fields, getattr(request.state, "request_id", None)),
    )
    await session.commit()
    await cache.invalidate(ticket_id)
    return TicketResponse.model_validate(ticket)


@router.post("/{ticket_id}/assign", response_model=TicketResponse)
async def assign_ticket(
    ticket_id: TicketId,
    payload: TicketAssignment,
    session: Session,
    cache: Cache,
    request: Request,
) -> TicketResponse:
    ticket = await session.scalar(
        update(Ticket)
        .where(Ticket.id == ticket_id, Ticket.version == payload.version)
        .values(
            assignee_id=payload.assignee_id,
            assignee_email=str(payload.assignee_email),
            version=Ticket.version + 1,
            updated_at=datetime.now(UTC),
        )
        .returning(Ticket)
        .execution_options(populate_existing=True, synchronize_session=False)
    )
    if ticket is None:
        exists = await session.get(Ticket, ticket_id)
        if exists is None:
            raise HTTPException(status_code=404, detail="Ticket not found")
        raise HTTPException(status_code=409, detail="Ticket changed; reload it and retry")
    add_to_outbox(
        session, ticket_assigned_event(ticket, getattr(request.state, "request_id", None))
    )
    await session.commit()
    await cache.invalidate(ticket_id)
    return TicketResponse.model_validate(ticket)


@router.post("/{ticket_id}/comments", response_model=CommentResponse, status_code=201)
async def add_comment(
    ticket_id: TicketId,
    payload: CommentCreate,
    session: Session,
    request: Request,
    x_user_id: Annotated[UUID, Header()],
    x_user_email: Annotated[EmailStr, Header(max_length=320)],
    actor_role: Annotated[str, Header(alias="X-User-Role", max_length=20)] = "client",
) -> CommentResponse:
    ticket = await session.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    comment = Comment(
        ticket_id=ticket_id,
        author_id=x_user_id,
        author_email=str(x_user_email),
        author_role=actor_role,
        body=payload.body,
        is_internal=payload.is_internal,
    )
    session.add(comment)
    await session.flush()
    add_to_outbox(
        session,
        ticket_commented_event(
            ticket, comment, actor_role, getattr(request.state, "request_id", None)
        ),
    )
    await session.commit()
    await session.refresh(comment)
    return CommentResponse.model_validate(comment)


@router.delete("/{ticket_id}", status_code=204)
async def delete_ticket(
    ticket_id: TicketId,
    session: Session,
    cache: Cache,
    request: Request,
) -> Response:
    result = await session.execute(delete(Ticket).where(Ticket.id == ticket_id))
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Ticket not found")
    add_to_outbox(
        session, ticket_deleted_event(ticket_id, getattr(request.state, "request_id", None))
    )
    await session.commit()
    await cache.invalidate(ticket_id)
    return Response(status_code=204)
