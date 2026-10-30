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

### Временный API тикетов, этап 2

До этапа 4 JWT и проверки ролей ещё не подключены. При создании обязательны
`X-User-Id` (UUID) и `X-User-Email` (email, максимум 320 символов). Это данные
автора для записи в БД и будущих событий, а не подтверждение личности.
Чтение, список и смена статуса пока доступны без заголовков; список показывает
все тикеты. Ограничение доступа по владельцу/роли будет добавлено вместе с JWT.

| Метод и путь | Запрос | Успех |
| --- | --- | --- |
| `POST /api/v1/tickets` | `{title, description, priority?}` и заголовки автора | `201`, тикет |
| `GET /api/v1/tickets/{id}` | Положительный BIGINT id | `200`, тикет |
| `GET /api/v1/tickets` | `page=1`, `size=20` (1–100); фильтры ниже | `200`, `{items, total, page, size}` |
| `POST /api/v1/tickets/{id}/status` | `{status, version}` | `200`, обновлённый тикет |

Тема обрезается по краям и должна содержать 1–200 символов. Описание также
обрезается по краям и не может быть пустым. Приоритет по умолчанию `medium`.
Начальные статус и версия — `open` и `1`; передавать их при создании нельзя.
Неизвестные поля тела запроса отклоняются с `422`.

Список сортируется по `created_at DESC, id DESC`. Поддержаны фильтры `status`,
`priority`, `author_id` (UUID), `assignee_id` (UUID), `escalated` (bool).
Фильтры объединяются через AND; total — число записей после фильтрации до
пагинации. У пустой страницы items пуст, но total сохраняется. Фильтра
`assignee=me` пока нет, поскольку текущий пользователь ещё не аутентифицируется.

Разрешённые переходы: open → in_progress/closed; in_progress → waiting_client/resolved;
waiting_client → in_progress; resolved → closed/in_progress. Из closed переходов нет.
До появления назначения и комментариев переходы в in_progress выполняются
напрямую через этот временный API. Повтор того же статуса запрещён.

При смене статуса version увеличивается на 1, updated_at обновляется. При resolved
выставляется resolved_at, при closed — closed_at; возврат в in_progress очищает
resolved_at/closed_at. При закрытии решённого тикета дата решения сохраняется.

`404` — тикет отсутствует; `409` — версия устарела (включая одновременное
обновление); `422` — неверные данные или запрещённый переход. Если одновременно
устарела версия и запрещён переход, приоритет имеет ошибка версии.

Кэш и публикация событий подключаются следующими задачами этапа 2.

### Общие соглашения

- Префикс `/api/v1/`, JSON, имена полей в `snake_case`.
- Аутентификация: `Authorization: Bearer <access_token>`.
- Ошибки в формате FastAPI: `{"detail": "текст ошибки"}`.
- Коды: `400` неверный запрос, `401` нет или плохой токен, `403` не та роль, `404` не найдено (в том числе чужой тикет для клиента), `409` конфликт версий или дубль, `422` ошибка валидации или запрещённый переход статуса, `429` превышен лимит.
- Списки: параметры `page` (с 1) и `size` (до 100), ответ `{"items": [...], "total": N, "page": P, "size": S}`.
- Каждый сервис отдаёт `GET /health/live`, `GET /health/ready`, `GET /metrics`; снаружи через шлюз они не видны.
