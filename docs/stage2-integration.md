> Current automatic publication behavior and the POST-to-email verification are documented in [ticket-event-publishing.md](ticket-event-publishing.md).

# Интеграция этапа 2 — пункт B4

## Что соединено

- Nginx принимает `/api/v1/tickets` и вложенные пути, сохраняя метод, тело,
  query-параметры и заголовки, включая временные `X-User-Id` и `X-User-Email`.
- `X-Request-ID` передаётся приложению; если клиент не прислал ID, его создаёт
  Nginx. Этот же ID записывается в JSON access-лог gateway.
- `ticket-service` находится в `edge`, `db-net`, `cache-net`, `broker-net`.
  Gateway подключён только к `edge`. Порты БД, кэша, AMQP и приложения наружу
  не опубликованы. Консоли RabbitMQ и Mailpit доступны только на localhost.
- Приложение ждёт PostgreSQL, миграции, Redis и RabbitMQ. Gateway ждёт
  `/health/ready` приложения. Сам этот маршрут остаётся внутренним.
- Миграции получают только настройки БД и находятся только в `db-net`.
- При явном обновлении RabbitMQ через Compose зависимые сервисы перезапускаются
  (`depends_on.restart: true`). Consumer также регистрирует пассивную очередь
  в robust-канале: после разрыва соединения восстанавливается сама подписка,
  а не только соединение. Readiness ждёт восстановления канала.
- `ticket-svc` не имеет configure/read-прав. Разрешена публикация в
  `helpdesk.events` только ключей `ticket.created`, `ticket.updated`,
  `ticket.status_changed`, `ticket.assigned`, `ticket.commented`, `ticket.deleted`.

## Настройки publisher для участника A

Compose передаёт `MQ_HOST`, `MQ_PORT`, `MQ_USER`, `MQ_PASSWORD`, `MQ_VHOST`,
`MQ_TIMEOUT`. Они доступны в `app.config.settings`; пароль хранится как SecretStr.
`TICKET_MQ_PASSWORD` из `.env` используется и RabbitMQ, и приложением.
Настройки предназначены для подключения A4:

```python
async with open_publisher(
    host=settings.mq_host,
    port=settings.mq_port,
    login=settings.mq_user,
    password=settings.mq_password.get_secret_value(),
    virtualhost=settings.mq_vhost,
    timeout=settings.mq_timeout,
) as publisher:
    # На этапе A4: сохранить в app.state и вызывать после COMMIT.
    ...
```

В текущем main publisher ещё не подключён к lifespan и операциям. Этот PR B4
не меняет бизнес-логику участника A. Успешный POST пока не означает отправку
события или письма. Сквозную автоматическую цепочку нужно проверить после A4.
Readiness ticket-service пока проверяет PostgreSQL, а не соединение publisher.

## Проверка API и кэша через Nginx

```bash
docker compose up -d --build --wait
docker compose ps
curl -i http://localhost/hello
curl -i http://localhost/health

curl -i -X POST http://localhost/api/v1/tickets \
  -H 'Content-Type: application/json' \
  -H 'X-User-Id: 06af7696-310a-47cf-a0b2-478d94d38e18' \
  -H 'X-User-Email: client@example.com' \
  -H 'X-Request-ID: demo-create' \
  -d '{"title":"Проверка интеграции","description":"Создано через Nginx","priority":"medium"}'
```

Ожидается `201`, JSON с тикетом и `X-Request-ID: demo-create`.
Подставьте ID из ответа вместо 42:

```bash
curl -i http://localhost/api/v1/tickets/42  # X-Cache: MISS
curl -i http://localhost/api/v1/tickets/42  # X-Cache: HIT
curl -i 'http://localhost/api/v1/tickets?size=1&page=1'
```

Смена статуса использует актуальную `version` из ответа (для нового тикета 1):

```bash
curl -i -X POST http://localhost/api/v1/tickets/42/status \
  -H 'Content-Type: application/json' \
  -d '{"status":"in_progress","version":1}'
curl -i http://localhost/api/v1/tickets/42  # снова MISS, новый статус
curl -i http://localhost/api/v1/tickets/42  # HIT
```

`/health/ready`, `/metrics`, `/docs` и `/api/v1/tickets-extra` снаружи возвращают
404. `/health` проверяет сам Nginx; `/hello` и API — путь до приложения.

После A4 дополнительно проверьте появление `ticket.created` и письма в Mailpit
при POST. Пока отправку можно проверить только отдельным вызовом publisher,
что не является автоматической отправкой из HTTP-операции.
