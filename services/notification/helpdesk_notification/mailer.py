import asyncio
import hashlib
from email.message import EmailMessage
from typing import Any

import aiosmtplib
from jinja2 import Environment, PackageLoader, StrictUndefined
from pydantic import BaseModel, EmailStr, Field

from helpdesk_common.events import EventEnvelope, EventType
from helpdesk_notification.config import Settings

TEMPLATES = {
    "ticket_created": {
        "subject": "Заявка #{{ ticket.ticket_id }} принята",
        "body": "ticket_created.txt",
    },
    "ticket_created_critical": {
        "subject": "Критичная заявка #{{ ticket.ticket_id }}",
        "body": "ticket_created_critical.txt",
    },
    "ticket_assigned": {
        "subject": "Заявка #{{ ticket.ticket_id }} назначена",
        "body": "ticket_assigned.txt",
    },
    "ticket_status_changed": {
        "subject": "Изменился статус заявки #{{ ticket.ticket_id }}",
        "body": "ticket_status_changed.txt",
    },
    "ticket_commented": {
        "subject": "Новый комментарий к заявке #{{ ticket.ticket_id }}",
        "body": "ticket_commented.txt",
    },
    "ticket_sla_breached": {
        "subject": "Нарушен SLA по заявке #{{ ticket.ticket_id }}",
        "body": "ticket_sla_breached.txt",
    },
}

template_environment = Environment(
    loader=PackageLoader("helpdesk_notification", "templates"),
    undefined=StrictUndefined,
    autoescape=False,
)


class TicketNotice(BaseModel):
    ticket_id: int = Field(gt=0)
    title: str = Field(min_length=1)
    priority: str | None = None
    author_email: EmailStr | None = None
    assignee_email: EmailStr | None = None
    new_status: str | None = None
    actor_role: str | None = None
    is_internal: bool | None = None
    kind: str | None = None
    due_at: str | None = None


def _unique_addresses(*addresses: str | EmailStr | None) -> list[str]:
    unique: dict[str, str] = {}
    for address in addresses:
        if address is not None:
            rendered = str(address).strip()
            unique.setdefault(rendered.casefold(), rendered)
    return list(unique.values())


def _notification_rules(
    event: EventEnvelope, ticket: TicketNotice, settings: Settings
) -> list[tuple[str, str]]:
    if event.event_type == EventType.TICKET_CREATED:
        if ticket.author_email is None:
            raise ValueError("ticket.created requires author_email")
        template = (
            "ticket_created_critical" if ticket.priority == "critical" else "ticket_created"
        )
        recipients = [str(ticket.author_email)]
        if ticket.priority == "critical":
            recipients.append(settings.oncall_email)
        return [(recipient, template) for recipient in _unique_addresses(*recipients)]

    if event.event_type == EventType.TICKET_ASSIGNED:
        return [
            (recipient, "ticket_assigned")
            for recipient in _unique_addresses(ticket.author_email, ticket.assignee_email)
        ]

    if event.event_type == EventType.TICKET_STATUS_CHANGED:
        if ticket.new_status is None or ticket.actor_role not in {"client", "agent", "admin"}:
            raise ValueError("ticket.status_changed requires new_status and actor_role")
        recipient = ticket.assignee_email if ticket.actor_role == "client" else ticket.author_email
        return [
            (address, "ticket_status_changed")
            for address in _unique_addresses(recipient)
        ]

    if event.event_type == EventType.TICKET_COMMENTED:
        if ticket.is_internal is None or ticket.actor_role not in {"client", "agent", "admin"}:
            raise ValueError("ticket.commented requires is_internal and actor_role")
        if ticket.is_internal:
            return []
        recipient = ticket.assignee_email if ticket.actor_role == "client" else ticket.author_email
        return [(address, "ticket_commented") for address in _unique_addresses(recipient)]

    if event.event_type == EventType.TICKET_SLA_BREACHED:
        if (
            ticket.kind not in {"response", "resolution"}
            or ticket.due_at is None
            or ticket.priority is None
        ):
            raise ValueError("ticket.sla_breached requires kind, due_at, and priority")
        recipients = _unique_addresses(settings.admin_notify_email, ticket.assignee_email)
        return [(recipient, "ticket_sla_breached") for recipient in recipients]

    raise ValueError(f"No notification rule for {event.routing_key}")


def build_messages(event: EventEnvelope, settings: Settings) -> list[tuple[str, EmailMessage]]:
    ticket = TicketNotice.model_validate(event.data)
    notifications = _notification_rules(event, ticket, settings)
    context: dict[str, Any] = {
        "ticket": ticket,
        "request_id": event.request_id,
        "event_id": str(event.event_id),
        "breach_label": "первой реакции" if ticket.kind == "response" else "решения",
    }
    output = []
    for recipient, template_name in notifications:
        templates = TEMPLATES[template_name]
        message = EmailMessage()
        message["From"] = settings.mail_from
        message["To"] = recipient
        message["Subject"] = template_environment.from_string(templates["subject"]).render(
            **context
        )
        recipient_id = hashlib.sha256(recipient.casefold().encode()).hexdigest()[:16]
        message["Message-ID"] = f"<{event.event_id}.{recipient_id}@helpdesk.local>"
        message["X-Helpdesk-Event-ID"] = str(event.event_id)
        if event.request_id:
            message["X-Request-ID"] = event.request_id
        message.set_content(template_environment.get_template(templates["body"]).render(**context))
        output.append((recipient, message))
    return output


class Mailer:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def send(self, message: EmailMessage) -> None:
        async with asyncio.timeout(self.settings.io_timeout):
            refused, _ = await aiosmtplib.send(
                message,
                hostname=self.settings.smtp_host,
                port=self.settings.smtp_port,
                timeout=self.settings.io_timeout,
                start_tls=False,
            )
            if refused:
                raise RuntimeError("SMTP refused a recipient")

    async def ping(self) -> None:
        async with asyncio.timeout(self.settings.io_timeout):
            async with aiosmtplib.SMTP(
                hostname=self.settings.smtp_host,
                port=self.settings.smtp_port,
                timeout=self.settings.io_timeout,
                start_tls=False,
            ) as smtp:
                await smtp.noop()


__all__ = ["Mailer", "TicketNotice", "build_messages"]
