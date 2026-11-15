from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from helpdesk_common import CurrentUser, get_current_user
from helpdesk_sla.db import get_session
from helpdesk_sla.domain import DEFAULT_POLICIES
from helpdesk_sla.models import SlaPolicy, SlaRecord

router = APIRouter(prefix="/api/v1/sla", tags=["sla"])


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


@router.get("/policies")
async def get_policies(
    session: Annotated[AsyncSession, Depends(get_session)],
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
) -> list[dict]:
    rows = await session.scalars(select(SlaPolicy))
    order = {priority: index for index, priority in enumerate(DEFAULT_POLICIES)}
    return [
        {
            "priority": row.priority,
            "response_minutes": row.response_minutes,
            "resolution_minutes": row.resolution_minutes,
        }
        for row in sorted(rows, key=lambda item: order.get(item.priority, len(order)))
    ]


@router.get("/tickets/{ticket_id}")
async def get_ticket_sla(
    ticket_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
) -> dict:
    record = await session.get(SlaRecord, ticket_id)
    if record is None:
        raise HTTPException(status_code=404, detail="SLA record not found")
    now = datetime.now(UTC)
    resolution_due = record.resolution_due
    if record.paused_at is not None:
        resolution_due += now - record.paused_at
    response_left = (
        0
        if record.responded_at is not None
        else max(0, int((record.response_due - now).total_seconds()))
    )
    resolution_left = (
        0
        if record.resolved_at is not None or record.closed
        else max(0, int((resolution_due - now).total_seconds()))
    )
    return {
        "ticket_id": record.ticket_id,
        "priority": record.priority,
        "response_due": iso(record.response_due),
        "resolution_due": iso(resolution_due),
        "responded_at": iso(record.responded_at),
        "resolved_at": iso(record.resolved_at),
        "response_breached": record.response_breached,
        "resolution_breached": record.resolution_breached,
        "response_time_left_sec": response_left,
        "resolution_time_left_sec": resolution_left,
        "time_left_sec": resolution_left,
    }


@router.get("/breaches")
async def get_breaches(
    session: Annotated[AsyncSession, Depends(get_session)],
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
    kind: Literal["response", "resolution"] | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[dict]:
    statement = select(SlaRecord).where(
        (SlaRecord.response_breached.is_(True))
        | (SlaRecord.resolution_breached.is_(True))
    )
    statement = statement.order_by(SlaRecord.created_at.desc()).offset(offset).limit(limit)
    records = list(await session.scalars(statement))

    breaches = []
    for record in records:
        for record_kind, breached, due_at in (
            ("response", record.response_breached, record.response_due),
            ("resolution", record.resolution_breached, record.resolution_due),
        ):
            if (
                breached
                and (kind is None or kind == record_kind)
                and (from_ is None or due_at >= from_)
                and (to is None or due_at <= to)
            ):
                breaches.append(
                    {
                        "ticket_id": record.ticket_id,
                        "title": record.title,
                        "priority": record.priority,
                        "kind": record_kind,
                        "due_at": iso(due_at),
                    }
                )
    return breaches


@router.get("/stats")
async def get_stats(
    session: Annotated[AsyncSession, Depends(get_session)],
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    period: Annotated[str, Query(pattern=r"^[1-9][0-9]{0,2}d$")] = "7d",
) -> dict:
    days = int(period[:-1])
    start = datetime.now(UTC) - timedelta(days=days)
    records = list(
        await session.scalars(select(SlaRecord).where(SlaRecord.created_at >= start))
    )

    response_measured = [
        row for row in records if row.responded_at is not None or row.response_breached
    ]
    resolution_measured = [
        row for row in records if row.resolved_at is not None or row.resolution_breached
    ]
    response_met = sum(
        row.responded_at is not None
        and row.responded_at <= row.response_due
        and not row.response_breached
        for row in response_measured
    )
    resolution_met = sum(
        row.resolved_at is not None
        and row.resolved_at <= row.resolution_due
        and not row.resolution_breached
        for row in resolution_measured
    )
    by_priority = {priority: 0 for priority in DEFAULT_POLICIES}
    for row in records:
        by_priority[row.priority] = by_priority.get(row.priority, 0) + int(
            row.response_breached or row.resolution_breached
        )

    def percentage(met: int, measured: int) -> float:
        return round(met * 100 / measured, 2) if measured else 0.0

    return {
        "period": period,
        "total": len(records),
        "response_met_pct": percentage(response_met, len(response_measured)),
        "resolution_met_pct": percentage(resolution_met, len(resolution_measured)),
        "breaches_by_priority": by_priority,
    }
