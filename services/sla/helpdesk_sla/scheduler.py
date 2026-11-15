import asyncio
from datetime import UTC, datetime
from time import monotonic
from uuid import NAMESPACE_URL, uuid5

from prometheus_client import Counter, Gauge, Histogram
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from helpdesk_common import get_logger
from helpdesk_common.events import EventEnvelope, EventType
from helpdesk_sla.models import SlaRecord
from helpdesk_sla.publisher import SlaPublisher

log = get_logger(__name__)
SLA_BREACHES_TOTAL = Counter(
    "sla_breaches_total", "SLA breaches published", ("kind", "priority")
)
SLA_CHECK_DURATION_SECONDS = Histogram(
    "sla_check_duration_seconds", "Duration of one SLA deadline check"
)
SLA_OPEN_RECORDS = Gauge("sla_open_records", "Number of active SLA records")


async def check_due_records(
    sessions: async_sessionmaker,
    publisher: SlaPublisher,
    *,
    now: datetime | None = None,
) -> int:
    now = now or datetime.now(UTC)
    statement = (
        select(SlaRecord)
        .where(
            SlaRecord.closed.is_(False),
            or_(
                (
                    SlaRecord.responded_at.is_(None)
                    | (SlaRecord.responded_at > SlaRecord.response_due)
                )
                & (SlaRecord.response_breached.is_(False))
                & (SlaRecord.response_due < now),
                (
                    SlaRecord.resolved_at.is_(None)
                    | (SlaRecord.resolved_at > SlaRecord.resolution_due)
                )
                & (SlaRecord.paused_at.is_(None))
                & (SlaRecord.resolution_breached.is_(False))
                & (SlaRecord.resolution_due < now),
            ),
        )
        .order_by(SlaRecord.ticket_id)
        .limit(100)
        .with_for_update(skip_locked=True)
    )

    published = 0
    published_labels: list[tuple[str, str]] = []
    async with sessions() as session:
        # Keep the flag update in the same transaction as the confirmed
        # publish. A retry after a DB rollback uses a stable event_id.
        async with session.begin():
            records = list(await session.scalars(statement))
            for record in records:
                if (
                    (record.responded_at is None or record.responded_at > record.response_due)
                    and not record.response_breached
                    and record.response_due < now
                ):
                    await publish_breach(publisher, record, "response", record.response_due, now)
                    record.response_breached = True
                    published += 1
                    published_labels.append(("response", record.priority))
                if (
                    (record.resolved_at is None or record.resolved_at > record.resolution_due)
                    and record.paused_at is None
                    and not record.resolution_breached
                    and record.resolution_due < now
                ):
                    await publish_breach(
                        publisher, record, "resolution", record.resolution_due, now
                    )
                    record.resolution_breached = True
                    published += 1
                    published_labels.append(("resolution", record.priority))
        open_count = await session.scalar(
            select(func.count()).select_from(SlaRecord).where(SlaRecord.closed.is_(False))
        )
    SLA_OPEN_RECORDS.set(open_count or 0)
    for kind, priority in published_labels:
        SLA_BREACHES_TOTAL.labels(kind=kind, priority=priority).inc()
    return published


async def publish_breach(
    publisher: SlaPublisher,
    record: SlaRecord,
    kind: str,
    due_at: datetime,
    occurred_at: datetime,
) -> None:
    due_at = due_at.astimezone(UTC)
    event_id = uuid5(
        NAMESPACE_URL,
        f"helpdesk:sla:{record.ticket_id}:{kind}:{due_at.isoformat()}",
    )
    event = EventEnvelope(
        event_id=event_id,
        event_type=EventType.TICKET_SLA_BREACHED,
        occurred_at=occurred_at,
        producer="sla-service",
        data={
            "ticket_id": record.ticket_id,
            "title": record.title,
            "kind": kind,
            "due_at": due_at.isoformat(),
            "priority": record.priority,
            "assignee_email": record.assignee_email,
        },
    )
    await publisher.publish(event)
    log.info(
        "sla_breach_published",
        event_id=str(event_id),
        ticket_id=record.ticket_id,
        kind=kind,
    )


async def run_scheduler(
    sessions: async_sessionmaker,
    publisher: SlaPublisher,
    interval: float,
) -> None:
    while True:
        started = monotonic()
        try:
            count = await check_due_records(sessions, publisher)
            if count:
                log.info("sla_breaches_checked", published=count)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("sla_scheduler_failed", reason=type(exc).__name__)
        finally:
            SLA_CHECK_DURATION_SECONDS.observe(monotonic() - started)
        await asyncio.sleep(interval)
