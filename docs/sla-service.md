# SLA service

`sla-service` tracks first response and resolution deadlines for tickets. It
consumes ticket events from RabbitMQ, stores its own state in `sla_db`, and
publishes `ticket.sla_breached` when a deadline is missed. It does not read or
write the ticket database.

## Default policies

| Priority | First response | Resolution |
| --- | ---: | ---: |
| critical | 15 minutes | 4 hours |
| high | 1 hour | 8 hours |
| medium | 4 hours | 24 hours |
| low | 8 hours | 72 hours |

The migration inserts these values into `sla_policies`. `SLA_TIME_SCALE=60`
shortens a configured minute to one second for a live demo; keep it at `1` for
normal operation. `SLA_CHECK_INTERVAL` controls how often the background task
looks for missed deadlines (default: 30 seconds).

## Event flow

The service consumes `ticket.created`, `ticket.assigned`,
`ticket.status_changed`, `ticket.updated`, and `ticket.deleted` from `sla.q`.
An event uses the common envelope from `libs/common`; its `data` contains
`ticket_id`, while creation also contains `title`, `priority`, and `created_at`.
Status changes include `new_status` (or `status`). Priority updates include
`priority`.

Each event ID is stored in `processed_events` in the same transaction as the
corresponding SLA change. The consumer acknowledges the RabbitMQ message only
after the transaction commits, so redelivery does not apply an event twice.
When a ticket waits for the customer, the resolution clock pauses. Returning to
`in_progress` resumes it and adds the pause duration to the deadline.

The scheduler locks up to 100 due rows with `FOR UPDATE SKIP LOCKED`, publishes
one event for each missed response or resolution deadline, then marks it as
published. Stable event IDs let downstream services safely ignore a duplicate
if RabbitMQ accepted a message just before a database transaction had to retry.

The event is routed to `notifications.q` and `ticket.sla.q`. RabbitMQ user
`sla-svc` can read only `sla.q` and publish only the `ticket.sla_breached`
routing key. The SLA container has no published host port. Its API is not yet
proxied by Nginx; authentication and gateway routing are added in a later PR.

## Internal API and monitoring

The service exposes these endpoints inside its Docker networks:

- `GET /api/v1/sla/policies`
- `GET /api/v1/sla/tickets/{ticket_id}`
- `GET /api/v1/sla/breaches?from=&to=&kind=`
- `GET /api/v1/sla/stats?period=7d`
- `GET /health/live`, `GET /health/ready`, and `GET /metrics`

`/metrics` exports `sla_breaches_total`, `sla_check_duration_seconds`, and
`sla_open_records` in Prometheus format.
