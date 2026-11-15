# Уведомления

Текущие правила писем, шаблоны, Redis-дедупликация, повторы и DLQ описаны в
[notification-delivery.md](notification-delivery.md).

`notification-service` не предоставляет публичное REST API. Он читает события
из RabbitMQ, отправляет письма через Mailpit и использует Redis только для
идемпотентности отправки.

## Запуск и просмотр

```bash
docker compose up -d --build --wait notification-service
docker compose logs --tail=50 notification-service
```

Письма доступны в Mailpit по адресу http://127.0.0.1:8025. SMTP-порт `1025`
используется только внутри Docker. Очередь ошибочных сообщений
`notifications.dlq` можно посмотреть через локальную консоль RabbitMQ:
http://127.0.0.1:15672.

## Проверки

```bash
python -m pip install -e 'libs/common[dev]' -e services/notification
python -m pytest -q services/notification/tests
```
