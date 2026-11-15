# Ticket event publishing (A4)

The ticket service opens one RabbitMQ publisher during FastAPI lifespan. Startup
fails if the configured broker connection cannot be established. Readiness checks
both PostgreSQL and the publisher connection.

Every successful ticket write commits first and then publishes a persistent
event with publisher confirms. The event follows the shared `EventEnvelope` contract and contains
`ticket_id`, `title`, `priority`, `author_id`, `author_email`, and `created_at`.
The originating `X-Request-ID` is copied into the envelope and RabbitMQ
correlation ID. When no request ID is provided, middleware generates one.

The API supports creation, edits, status changes, assignment, comments, and
deletion. Each write publishes its matching event after `COMMIT`; failures before
commit do not publish. Status and comment events carry `X-User-Role` (defaults to
`client` while the temporary identity headers are in use). `ticket.updated`
contains a sorted list of changed field names; when priority changes it also
contains the new `priority` value for SLA recalculation.

The ticket service also consumes `ticket.sla_breached` from the infrastructure
provisioned queue named by `MQ_SLA_QUEUE` (default `ticket.sla.q`). It records
the event id in `processed_events` and the applied field changes in
`ticket_history` in the same PostgreSQL transaction. A repeated event id is
acknowledged without applying the change again. A response breach raises the
priority by one level, up to `critical`; a resolution breach only marks the
ticket escalated. After commit, the ticket cache is invalidated.

The RabbitMQ queue and `ticket-svc` read permission are provisioned by the
infrastructure change. The consumer uses passive queue lookup and does not
declare or modify broker topology.

## Local end-to-end check

Start the stack with a configured `.env`:

```bash
docker compose up -d --build --wait
```

Create a ticket through Nginx, then inspect the response and Mailpit inbox at
`http://localhost:8025`:

```bash
curl -i -X POST http://localhost/api/v1/tickets \
  -H 'Content-Type: application/json' \
  -H 'X-User-Id: 06af7696-310a-47cf-a0b2-478d94d38e18' \
  -H 'X-User-Email: client@example.com' \
  -H 'X-Request-ID: a4-create-check' \
  -d '{"title":"A4 event check","description":"Created through the API","priority":"medium"}'
```

The API should return `201`. The Mailpit message should have the supplied email
as recipient and reference the created ticket. The event should carry the same
request ID. `GET /health/ready` on ticket-service checks PostgreSQL and
RabbitMQ; the route is internal and is not exposed by Nginx.

Publishing happens after the database commit. If RabbitMQ fails at that point,
the ticket remains committed and the request reports a publication failure.
There is no transactional outbox or automatic retry yet, so a client retry can
create a duplicate ticket.
