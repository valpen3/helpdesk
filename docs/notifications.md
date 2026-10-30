# Notification-service и Mailpit — этап 2, B3

Первая версия обрабатывает `ticket.created`: получает событие из
`notifications.q`, логирует его ID и отправляет простое текстовое письмо автору.
Подключение publisher к операциям и маршруты Nginx выполняются отдельно в пункте 4.

## Запуск

В `.env` должны быть заданы `RABBITMQ_ADMIN_PASSWORD` и `NOTIF_MQ_PASSWORD`.
RabbitMQ создаёт `notif-svc` с правом читать только `notifications.q`, без прав
создавать объекты или публиковать сообщения. Конфигурация хеширует оба пароля
при запуске; `.env` не отправляется в Git.

```bash
docker compose up -d --build --wait notification-service
docker compose ps
docker compose logs --tail=50 notification-service
```

Mailpit открывается на http://127.0.0.1:8025. SMTP-порт `1025` доступен только
в Docker; письма не отправляются реальным адресатам. В этой версии Mailpit
не имеет постоянного тома, при пересоздании его контейнера письма теряются.

Сервис уведомлений подключён только к `broker-net` и `mail-net`, не имеет
доступа к PostgreSQL и публичного HTTP API. Маленький внутренний HTTP-сервер
предоставляет `/health/live` и `/health/ready`. Готовность проверяет consumer
и SMTP; Swagger отключён. Процесс работает от пользователя app, не root.

## Проверка письма без операций с БД

1. Открыть http://127.0.0.1:15672 и войти как admin с паролем из `.env`.
2. Exchanges → `helpdesk.events` → Publish message.
3. Routing key: `ticket.created`. Payload:

```json
{
  "event_id": "ef6a432a-ccf9-46f1-9eaf-909e9cdcbf91",
  "event_type": "ticket.created",
  "occurred_at": "2026-10-30T12:00:00Z",
  "producer": "ticket-service",
  "request_id": "manual-demo",
  "data": {
    "ticket_id": 42,
    "title": "Не работает VPN",
    "priority": "high",
    "author_id": "06af7696-310a-47cf-a0b2-478d94d38e18",
    "author_email": "client@example.com",
    "created_at": "2026-10-30T12:00:00Z"
  }
}
```

4. Опубликовать сообщение и открыть Mailpit. Ожидается письмо
   «Обращение #42 принято» для client@example.com.
5. В логах consumer видны `event_received` и `notification_sent`.

Это тестовое событие: запись тикета в БД не создаётся. Копия также попадёт в
`sla.q` и останется там, пока не появится sla-service.

## Подтверждения и ошибки

- `prefetch_count=10` ограничивает число неподтверждённых сообщений; обработка
  в первой версии последовательная.
- `ack` отправляется только после принятия письма SMTP-сервером.
- Ошибка SMTP/таймаут → пауза 5 секунд → `reject(requeue=True)`. При восстановлении
  SMTP событие можно обработать снова. Лимита попыток в MVP нет.
- Невалидный JSON, неизвестный тип конверта или неверные обязательные данные
  `ticket.created` → лог и `reject(requeue=False)`. DLQ пока нет, сообщение удаляется.
- Остальные известные типы событий пока подтверждаются без письма с логом
  `notification_not_implemented`. Полные правила рассылки относятся к этапу 4.
- При выключении consumer неподтверждённые сообщения возвращаются брокеру.
  Соединение восстанавливается через robust-клиент; при сбое запуска попытка
  повторяется с паузой. SMTP и открытие соединения ограничены таймаутами.

SMTP и RabbitMQ не участвуют в общей транзакции. После отправки письма процесс
может завершиться до ack, поэтому повторная доставка может дать дубль письма.
`Message-ID` и `X-Helpdesk-Event-ID` помогают сопоставить письмо с событием,
но не обеспечивают идемпотентность. Redis, шаблоны, полный набор получателей,
retry-очередь и DLQ добавляются на этапе 4.

## Тесты

```bash
python -m pip install -e 'libs/common[dev]' -e services/notification
python -m pytest -q services/notification/tests
```

Проверяются порядок SMTP → ack, содержимое письма, повреждённые события,
возврат при сбое SMTP, отмена обработки и ограничение первой версии.
