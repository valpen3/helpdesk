# Ticket event publishing (A4)

The ticket service opens one RabbitMQ publisher during FastAPI lifespan. Startup
fails if the configured broker connection cannot be established. Readiness checks
both PostgreSQL and the publisher connection.

`POST /api/v1/tickets` commits the ticket first, refreshes its database-generated
fields, and then publishes a persistent `ticket.created` event with publisher
confirms. The event follows the shared `EventEnvelope` contract and contains
`ticket_id`, `title`, `priority`, `author_id`, `author_email`, and `created_at`.
The originating `X-Request-ID` is copied into the envelope and RabbitMQ
correlation ID. When no request ID is provided, middleware generates one.

The notification consumer sends the confirmation email for `ticket.created`.
Status changes do not publish events yet: their contract requires the actor role,
which the current temporary API does not identify.

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
