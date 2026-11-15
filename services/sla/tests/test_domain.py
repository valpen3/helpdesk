from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from helpdesk_common.events import EventEnvelope, EventType
from helpdesk_sla.domain import DEFAULT_POLICIES, apply_ticket_event, create_sla_record
from helpdesk_sla.models import SlaPolicy


@pytest.fixture
def policies() -> dict[str, SlaPolicy]:
    return {
        priority: SlaPolicy(
            priority=priority,
            response_minutes=response,
            resolution_minutes=resolution,
        )
        for priority, (response, resolution) in DEFAULT_POLICIES.items()
    }


def make_event(event_type: EventType, data: dict, occurred_at: datetime) -> EventEnvelope:
    return EventEnvelope(
        event_type=event_type,
        producer="ticket-service",
        occurred_at=occurred_at,
        data=data,
    )


def test_create_record_applies_priority_policy_and_demo_time_scale(policies) -> None:
    created_at = datetime(2026, 1, 1, tzinfo=UTC)
    event = make_event(
        EventType.TICKET_CREATED,
        {
            "ticket_id": 12,
            "title": "Cannot log in",
            "priority": "critical",
            "created_at": created_at.isoformat(),
        },
        created_at,
    )

    record = create_sla_record(event, policies, time_scale=60)

    assert record.response_due == created_at + timedelta(seconds=15)
    assert record.resolution_due == created_at + timedelta(seconds=240)


def test_waiting_client_pause_shifts_resolution_deadline(policies) -> None:
    created_at = datetime(2026, 1, 1, tzinfo=UTC)
    record = SimpleNamespace(
        ticket_id=12,
        title="Cannot log in",
        priority="critical",
        created_at=created_at,
        response_due=created_at + timedelta(minutes=15),
        resolution_due=created_at + timedelta(hours=4),
        responded_at=None,
        resolved_at=None,
        paused_at=None,
        paused_total_sec=0,
        response_breached=False,
        resolution_breached=False,
        closed=False,
    )
    pause_start = created_at + timedelta(minutes=30)
    pause_end = pause_start + timedelta(hours=2)

    apply_ticket_event(
        record,
        make_event(EventType.TICKET_STATUS_CHANGED, {"new_status": "waiting_client"}, pause_start),
        policies,
        time_scale=1,
    )
    apply_ticket_event(
        record,
        make_event(EventType.TICKET_STATUS_CHANGED, {"new_status": "in_progress"}, pause_end),
        policies,
        time_scale=1,
    )

    assert record.responded_at == pause_end
    assert record.paused_at is None
    assert record.paused_total_sec == 7200
    assert record.resolution_due == created_at + timedelta(hours=6)


def test_priority_change_does_not_extend_an_elapsed_deadline(policies) -> None:
    created_at = datetime(2026, 1, 1, tzinfo=UTC)
    record = SimpleNamespace(
        ticket_id=12,
        title="Cannot log in",
        priority="critical",
        created_at=created_at,
        response_due=created_at + timedelta(minutes=15),
        resolution_due=created_at + timedelta(hours=4),
        responded_at=None,
        resolved_at=None,
        paused_at=None,
        paused_total_sec=0,
        response_breached=False,
        resolution_breached=False,
        closed=False,
    )
    updated_at = created_at + timedelta(minutes=20)

    apply_ticket_event(
        record,
        make_event(EventType.TICKET_UPDATED, {"priority": "low"}, updated_at),
        policies,
        time_scale=1,
    )

    assert record.priority == "low"
    assert record.response_due == created_at + timedelta(minutes=15)
    assert record.resolution_due == created_at + timedelta(hours=72)
