# Notification delivery

`notification-service` is an event consumer, not a public REST API. It reads
`notifications.q`, renders a plain-text email for each recipient, and sends it
through Mailpit/SMTP.

## Rules

| Event | Recipients | Template/rule |
| --- | --- | --- |
| `ticket.created` | Ticket author | Confirmation; critical tickets use the critical template and also notify `ONCALL_EMAIL` |
| `ticket.assigned` | Author and assignee | Assignment notice; duplicate addresses are collapsed |
| `ticket.status_changed` | Assignee when the client changed status; otherwise author | Includes `new_status` |
| `ticket.commented` | Assignee for client comments; otherwise author | Internal comments are never emailed |
| `ticket.sla_breached` | `ADMIN_NOTIFY_EMAIL` and current assignee, if any | Includes breach type, deadline, and priority |

Templates are separate Jinja2 text files in
`services/notification/helpdesk_notification/templates/`. Each message gets a
stable `Message-ID` derived from the event ID and recipient.

## Duplicate delivery

RabbitMQ may redeliver an event if a worker disconnects before acknowledging
it. Before sending to a recipient, the service claims a short Redis lock under
`notif:lock:*`. After SMTP accepts the email, it writes `notif:done:*` for
`NOTIF_IDEMPOTENCY_TTL` seconds (default: seven days) and acknowledges the
event. Completed deliveries are skipped on redelivery. If one event has several
recipients, each recipient has a separate key, so a retry does not resend email
to recipients already completed.

SMTP and Redis cannot participate in one transaction. A crash after SMTP
accepts an email but before Redis records completion can still cause a repeat;
the stable `Message-ID` helps identify that case. The Redis lock expires after
`NOTIF_IDEMPOTENCY_LOCK_TTL` seconds so a crashed worker does not block future
attempts forever.

## Retry and dead-letter path

On a temporary SMTP or Redis failure, the consumer publishes a persistent copy
to `notifications.retry` with an incremented `x-attempt` header, waits for the
broker confirmation, and only then acknowledges the original. The message waits
in `notifications.retry.q` for `NOTIF_RETRY_DELAY_MS` (default: 30 seconds),
then returns only to `notifications.q`; it is not re-broadcast to SLA or
ticket-service. After three retries it is published to `notifications.dead`
and stored in `notifications.dlq`. Invalid event payloads go directly to the
dead-letter queue. RabbitMQ user `notif-svc` can read the main queue and publish
only to the retry and dead-letter exchanges.

`notifications.dlq` can be inspected in the RabbitMQ management console on
`http://localhost:15672`. The configured retry delay, maximum attempts, and
deduplication TTL are in `.env.example`.

## Gateway

Nginx proxies `/api/v1/auth/...` and `/api/v1/users...` to `auth-service`,
and `/api/v1/sla...` to `sla-service`. Before proxying SLA requests, Nginx
checks the bearer access token with `auth-service`. Fine-grained role checks
remain the responsibility of the SLA API.
Nginx also applies per-IP login and ticket-creation limits, a connection limit,
and common response-security headers.
