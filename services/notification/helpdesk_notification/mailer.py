import asyncio
from email.message import EmailMessage

import aiosmtplib
from pydantic import BaseModel, EmailStr, Field

from helpdesk_common.events import EventEnvelope
from helpdesk_notification.config import Settings


class TicketCreated(BaseModel):
    ticket_id: int = Field(gt=0)
    title: str = Field(min_length=1)
    author_email: EmailStr


def build_message(event: EventEnvelope, sender: str) -> EmailMessage:
    data = TicketCreated.model_validate(event.data)
    message = EmailMessage()
    message["From"] = sender
    message["To"] = str(data.author_email)
    message["Subject"] = f"Обращение #{data.ticket_id} принято"
    message["Message-ID"] = f"<{event.event_id}@helpdesk.local>"
    message["X-Helpdesk-Event-ID"] = str(event.event_id)
    message.set_content(
        f"Ваше обращение #{data.ticket_id} принято.\n\n{data.title}\n"
        "Сотрудник поддержки рассмотрит его.\n"
    )
    return message


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
