# Контракты HelpDesk

Этот файл — договорённость между участниками. Меняем только через pull request, который одобрили оба.

## Формат событий

Все события публикуются в exchange `helpdesk.events` (тип topic). Routing key совпадает с `event_type`.

```json
{
  "event_id": "7f1c2b9e-1d2a-4c55-9a7e-0b8f3c6d5e4a",
  "event_type": "ticket.created",
  "occurred_at": "2026-11-01T10:00:00Z",
  "producer": "ticket-service",
  "request_id": "3f9a0c1e8b7d4a2f",
  "data": {
    "ticket_id": 42,
    "title": "Не работает VPN",
    "priority": "high",
    "author_id": "0b6c...",
    "author_email": "client@example.com",
    "created_at": "2026-11-01T10:00:00Z"
  }
}
```

| Поле | Тип | Смысл |
| --- | --- | --- |
| `event_id` | UUID | Уникальный id события; по нему потребители отбрасывают дубли |
| `event_type` | строка | Тип события, он же routing key |
| `occurred_at` | ISO 8601, UTC | Когда событие произошло (по нему считается задержка уведомлений) |
| `producer` | строка | Сервис-отправитель |
| `request_id` | строка или null | `X-Request-ID` HTTP-запроса, который породил событие |
| `data` | объект | Данные события, состав зависит от типа |

Свойства сообщения в RabbitMQ: `delivery_mode=2` (persistent), `content_type=application/json`, `message_id=event_id`.

Модель в коде: `helpdesk_common.events.EventEnvelope`.

## Типы событий

| Routing key | Продюсер | Потребители | Поля `data` |
| --- | --- | --- | --- |
| `ticket.created` | ticket-service | notification, sla | `ticket_id, title, priority, author_id, author_email, created_at` |
| `ticket.updated` | ticket-service | sla | `ticket_id, title, changed_fields, author_email` (+ `priority`, если менялся) |
| `ticket.status_changed` | ticket-service | notification, sla | `ticket_id, title, old_status, new_status, author_email, assignee_email, actor_role` |
| `ticket.assigned` | ticket-service | notification, sla | `ticket_id, title, assignee_id, assignee_email, author_email` |
| `ticket.commented` | ticket-service | notification | `ticket_id, title, comment_id, is_internal, actor_role, author_email, assignee_email` |
| `ticket.deleted` | ticket-service | sla | `ticket_id` |
| `ticket.sla_breached` | sla-service | notification, ticket | `ticket_id, title, kind (response \| resolution), due_at, priority` |

## Правила изменения контракта

1. Добавлять новые поля в `data` можно: потребители игнорируют неизвестные поля.
2. Переименовывать и удалять поля нельзя. Если очень нужно — новый тип события, старый поддерживается, пока все потребители не перейдут.
3. Потребитель не должен ходить в чужую БД за недостающими данными: если поля не хватает, его добавляет продюсер.
4. Обработка каждого события идемпотентна: повторная доставка того же `event_id` ничего не меняет.

## Соглашения по HTTP API

- Префикс `/api/v1/`, JSON, имена полей в `snake_case`.
- Аутентификация: `Authorization: Bearer <access_token>`.
- Ошибки в формате FastAPI: `{"detail": "текст ошибки"}`.
- Коды: `400` неверный запрос, `401` нет или плохой токен, `403` не та роль, `404` не найдено (в том числе чужой тикет для клиента), `409` конфликт версий или дубль, `422` ошибка валидации или запрещённый переход статуса, `429` превышен лимит.
- Списки: параметры `page` (с 1) и `size` (до 100), ответ `{"items": [...], "total": N, "page": P, "size": S}`.
- Каждый сервис отдаёт `GET /health/live`, `GET /health/ready`, `GET /metrics`; снаружи через шлюз они не видны.
