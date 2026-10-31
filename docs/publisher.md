> Current automatic publication behavior and the POST-to-email verification are documented in [ticket-event-publishing.md](ticket-event-publishing.md).

# Интерфейс publisher — этап 2, B2

Предложение интерфейса для ревью участником A. На этом шаге модуль готовится
отдельно: HTTP-операции, FastAPI lifespan, настройки сервиса и Compose пока
не подключают publisher. Интеграция выполняется отдельным PR в пункте 4.

## Вызов из бизнес-операции

```python
from app.messaging.publisher import EventPublisher
from helpdesk_common.events import EventEnvelope, EventType


async def publish_created(publisher: EventPublisher, ticket, request_id: str):
    # Вызывающий код уже выполнил await session.commit().
    event = EventEnvelope(
        event_type=EventType.TICKET_CREATED,
        producer="ticket-service",
        request_id=request_id,
        data={
            "ticket_id": ticket.id,
            "title": ticket.title,
            "priority": ticket.priority,
            "author_id": str(ticket.author_id),
            "author_email": ticket.author_email,
            "created_at": ticket.created_at.isoformat(),
        },
    )
    await publisher.publish(event)
```

`publish(event: EventEnvelope) -> None` завершается успешно после подтверждения
RabbitMQ. `data` должен содержать JSON-совместимые значения и поля из `docs/api.md`;
publisher не извлекает данные из БД и не дополняет событие автоматически.

Сообщение отправляется в существующий `helpdesk.events`:

- routing key и свойство `type` равны `event.event_type`;
- `delivery_mode=2`, `content_type=application/json`;
- `message_id` равен `event_id`, `correlation_id` — `request_id`;
- тело содержит весь `EventEnvelope`, время события передаётся и в AMQP timestamp;
- publisher confirms включены;
- `mandatory=True` и `on_return_raises=True`: отсутствие подходящей очереди
  считается ошибкой, а не успешной доставкой.

## Жизненный цикл

`open_publisher(host=..., login=..., password=..., port=5672, virtualhost="/",
timeout=5.0)` — асинхронный контекстный менеджер. На этапе интеграции его нужно
открыть один раз в lifespan, сохранить publisher в `app.state`, использовать
во всех операциях и закрыть при остановке приложения.

```python
async with open_publisher(host=host, login=login, password=password) as publisher:
    # Соединение открыто. Несколько операций используют этот publisher.
    await publisher.publish(event)
```

Логин и пароль передаются из настроек/окружения; их нельзя записывать в код.
Модуль не создаёт пользователей, exchange, очереди или привязки. `get_exchange`
используется без декларации, поэтому publisher не требует права configure.
`connect_robust` восстанавливает соединение в фоне, но не переотправляет событие.

## Ошибки и ответственность вызывающего кода

Ошибки открытия соединения передаются вызывающему коду. Ошибки отправки,
возврат сообщения, разрыв соединения и таймаут публикации преобразуются в
`EventPublishError`; исходная причина доступна через `__cause__`.
Отмена asyncio-задачи не подавляется. Соединение закрывается и при ошибке
подготовки канала или вызывающего кода.

`timeout` должен быть положительным. Он ограничивает открытие publisher и
каждую публикацию, включая ожидание восстановления соединения.

Publisher не повторяет отправку автоматически. При ошибке подтверждение могло
потеряться после принятия сообщения брокером. При осознанном повторе нужно
использовать тот же `EventEnvelope`/`event_id`; потребитель должен отсеивать дубли.

Успешный confirm означает принятие брокером, а не обработку потребителем.
Ошибка публикации после COMMIT не откатывает запись тикета. Вызывающий код
должен определить ответ HTTP и журналирование ошибки; не следует сообщать, что
тикет не создан, если транзакция уже завершилась. Атомарность записи и события
будет решаться через transactional outbox на этапе надёжности.

## Проверка модуля

```bash
python -m pytest -q services/ticket/tests/test_publisher.py
```

Тесты не требуют RabbitMQ: проверяют свойства сообщения, отсутствие скрытых
повторов, таймаут и освобождение соединения при ошибках. Полный сквозной сценарий
будет добавлен после подключения операций и consumer.
