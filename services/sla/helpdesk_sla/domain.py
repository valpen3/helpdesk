from datetime import UTC, datetime, timedelta

from helpdesk_common.events import EventEnvelope, EventType
from helpdesk_sla.models import SlaPolicy, SlaRecord

PRIORITIES = ("low", "medium", "high", "critical")
DEFAULT_POLICIES = {
    "critical": (15, 240),
    "high": (60, 480),
    "medium": (240, 1440),
    "low": (480, 4320),
}


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def parse_datetime(value: object, field: str) -> datetime:
    if isinstance(value, datetime):
        return as_utc(value)
    if isinstance(value, str):
        try:
            return as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError as exc:
            raise ValueError(f"Invalid {field}") from exc
    raise ValueError(f"Missing or invalid {field}")


def required_int(data: dict, field: str) -> int:
    value = data.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value > 2**63 - 1:
        raise ValueError(f"Missing or invalid {field}")
    if value < 1:
        raise ValueError(f"Missing or invalid {field}")
    return value


def policy_duration(minutes: int, time_scale: float) -> timedelta:
    # At the default scale, a configured minute is a real minute. Setting
    # SLA_TIME_SCALE=60 turns a 15-minute deadline into a 15-second demo.
    return timedelta(seconds=minutes * 60 / time_scale)


def policies_by_priority(policies: list[SlaPolicy]) -> dict[str, SlaPolicy]:
    return {policy.priority: policy for policy in policies}


def create_sla_record(
    event: EventEnvelope,
    policies: dict[str, SlaPolicy],
    time_scale: float,
) -> SlaRecord:
    data = event.data
    ticket_id = required_int(data, "ticket_id")
    title = data.get("title")
    priority = data.get("priority")
    if not isinstance(title, str) or not title:
        raise ValueError("Missing or invalid title")
    if not isinstance(priority, str):
        raise ValueError("Missing or invalid priority")
    policy = policies.get(priority)
    if policy is None:
        raise ValueError("Missing or invalid priority")
    created_at = parse_datetime(data.get("created_at"), "created_at")
    return SlaRecord(
        ticket_id=ticket_id,
        title=title,
        priority=priority,
        created_at=created_at,
        response_due=created_at + policy_duration(policy.response_minutes, time_scale),
        resolution_due=created_at + policy_duration(policy.resolution_minutes, time_scale),
    )


def apply_ticket_event(
    record: SlaRecord,
    event: EventEnvelope,
    policies: dict[str, SlaPolicy],
    time_scale: float,
) -> None:
    data = event.data
    occurred_at = as_utc(event.occurred_at)

    if event.event_type == EventType.TICKET_ASSIGNED:
        record.responded_at = record.responded_at or occurred_at
        return

    if event.event_type == EventType.TICKET_STATUS_CHANGED:
        new_status = data.get("new_status", data.get("status"))
        if new_status == "in_progress":
            record.responded_at = record.responded_at or occurred_at
            if record.paused_at is not None:
                pause_seconds = max(
                    0, int((occurred_at - as_utc(record.paused_at)).total_seconds())
                )
                record.paused_total_sec += pause_seconds
                record.resolution_due = as_utc(record.resolution_due) + timedelta(
                    seconds=pause_seconds
                )
                record.paused_at = None
        elif new_status == "waiting_client":
            record.paused_at = record.paused_at or occurred_at
        elif new_status == "resolved":
            record.resolved_at = record.resolved_at or occurred_at
        elif new_status == "closed":
            record.closed = True
        return

    if event.event_type == EventType.TICKET_UPDATED:
        priority = data.get("priority")
        if priority is None or priority == record.priority:
            return
        if not isinstance(priority, str):
            raise ValueError("Missing or invalid priority")
        policy = policies.get(priority)
        if policy is None:
            raise ValueError("Missing or invalid priority")
        record.priority = priority
        if (
            record.responded_at is None
            and not record.response_breached
            and as_utc(record.response_due) > occurred_at
        ):
            record.response_due = as_utc(record.created_at) + policy_duration(
                policy.response_minutes, time_scale
            )
        if (
            record.resolved_at is None
            and not record.resolution_breached
            and as_utc(record.resolution_due) > occurred_at
        ):
            record.resolution_due = as_utc(record.created_at) + policy_duration(
                policy.resolution_minutes, time_scale
            ) + timedelta(seconds=record.paused_total_sec)
        return

    if event.event_type == EventType.TICKET_DELETED:
        record.closed = True


