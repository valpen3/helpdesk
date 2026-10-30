# HelpDesk

Учебный проект: система обращений в техподдержку в микросервисной архитектуре
(FastAPI, PostgreSQL, Redis, RabbitMQ, Nginx).

Полное описание архитектуры и план работ — в `docs/architecture.md`.
Контракт событий между сервисами — в `docs/api.md`.

## Быстрый старт (этап 0)

```bash
cp .env.example .env      # заполнить своими значениями, в git не коммитится
make venv                 # виртуальное окружение + общий пакет libs/common
make test                 # тесты общего пакета
make lint                 # проверка стиля
```

## Redis (первая часть этапа 1)

Compose запускает Nginx, ticket-service, PostgreSQL, Redis и RabbitMQ.
Команды выполняются из корня репозитория, в Windows — из Ubuntu с интеграцией
Docker Desktop. Перед первым запуском создайте `.env` из `.env.example`,
если файла ещё нет, и задайте свой `RABBITMQ_ADMIN_PASSWORD`. Compose требует
эту переменную даже при запуске только Redis, поскольку проверяет весь файл.

```bash
docker compose up -d --wait redis
docker compose ps
docker compose exec redis redis-cli ping  # ожидается PONG
```

Настройки находятся в `infra/redis/redis.conf`: AOF включён, синхронизация на диск
раз в секунду, лимит данных 256 МБ. Политика `volatile-lru` вытесняет давно
неиспользовавшиеся ключи только среди ключей с TTL. Если таких ключей нет,
при достижении лимита новые записи могут отклоняться.

Данные хранятся в именованном томе `redis-data`, независимо от контейнера.
Проверка сохранения после пересоздания (временный ключ живёт 10 минут):

```bash
docker compose exec redis redis-cli SET helpdesk:setup-check persisted EX 600
docker compose up -d --force-recreate --wait redis
docker compose exec redis redis-cli GET helpdesk:setup-check  # persisted
docker compose exec redis redis-cli DEL helpdesk:setup-check
```

Redis доступен контейнерам в сети `cache-net` по адресу `redis:6379`.
Сеть внутренняя (`internal: true`), порт на хост не опубликован. На этапе 1
аутентификации нет; ACL добавляются на этапе 3. Будущие сервисы, использующие
Redis, нужно подключить к `cache-net` и настроить ожидание `service_healthy`.

`docker compose down` сохраняет том. `docker compose down -v` удаляет его вместе
с данными. При аварийном отказе AOF с `everysec` допускает потерю примерно
последней секунды изменений.

## RabbitMQ (этап 1)

```bash
docker compose up -d --wait rabbitmq
docker compose ps
docker compose exec rabbitmq rabbitmq-diagnostics -q ping
```

Образ `rabbitmq:3.13-management` включает брокер сообщений и веб-консоль.
Консоль: http://127.0.0.1:15672, логин `admin`, пароль — значение
`RABBITMQ_ADMIN_PASSWORD` из локального `.env`. Не отправляйте `.env` в Git.
Остальные значения из шаблона нужно заменить перед использованием соответствующих
компонентов.

Порт консоли опубликован только на `127.0.0.1`. AMQP-порт `5672` на хост не
публикуется: будущие сервисы подключаются к `rabbitmq:5672` через внутреннюю
сеть `broker-net`. Сеть `admin-net` нужна для административного доступа.
Учётная запись `admin` предназначена для консоли; отдельные учётки сервисов
с ограниченными правами добавляются на этапе 3.

Том `rabbitmq-data` хранит состояние узла в `/var/lib/rabbitmq`, а постоянный
`hostname: rabbitmq` сохраняет его имя при пересоздании контейнера. Пользователь
и пароль администратора задаются через определения при каждом запуске:
изменение `.env` применяется после пересоздания контейнера RabbitMQ.
`docker compose down -v` удаляет тома PostgreSQL, Redis и RabbitMQ вместе с данными.

Healthcheck проверяет доступность узла и запуск приложения RabbitMQ
(`ping` и `check_running`), а не доставку сообщений.

### Маршрутизация событий (первый шаг этапа 2)

`infra/rabbitmq/rabbitmq.conf` подключает импорт определений при запуске.
`infra/rabbitmq/definitions.json` описывает виртуальный хост
`/`, постоянный topic-exchange `helpdesk.events`, очереди `notifications.q` и
`sla.q`, а также привязки из `docs/api.md`. Паролей в этих файлах нет.

При импорте определений на пустом узле RabbitMQ не создаёт пользователя из
`RABBITMQ_DEFAULT_USER` автоматически. Поэтому `start.sh` перед запуском
вычисляет солёный SHA-256 хеш пароля из `.env` и подставляет его вместо
`__ADMIN_PASSWORD_HASH__` в копию JSON внутри контейнера. Исходный файл остаётся
неизменным. Копия доступна только владельцу, процессу RabbitMQ. В определения
включены администратор и его права; пользователи сервисов добавляются на этапе 3.
Вручную импортировать исходный JSON с маркером хеша не нужно.

| Routing key | notifications.q | sla.q |
| --- | --- | --- |
| `ticket.created` | да | да |
| `ticket.assigned` | да | да |
| `ticket.status_changed` | да | да |
| `ticket.commented` | да | нет |
| `ticket.updated` | нет | да |
| `ticket.deleted` | нет | да |
| `ticket.sla_breached` | да | нет |

Обе очереди пока classic; quorum и кластер добавляются на этапе надёжности.
`durable: true` сохраняет объявление очереди/exchange, `auto_delete: false`
не удаляет их при отключении потребителей. Для сохранения сообщений отправитель
также должен использовать persistent-сообщения и publisher confirms.
Очередь `ticket.sla.q` добавляется вместе с обработчиком эскалации на этапе 4.

После изменения конфигурации примените её и проверьте объекты:

```bash
docker compose up -d --force-recreate --wait rabbitmq
docker compose exec rabbitmq rabbitmqctl list_exchanges name type durable
docker compose exec rabbitmq rabbitmqctl list_queues name durable messages_ready messages_unacknowledged
docker compose exec rabbitmq rabbitmqctl list_bindings source_name destination_name routing_key
```

В веб-консоли проверьте вкладки Exchanges и Queues and Streams. Пока потребителей
нет, сообщения остаются в очередях. Публикация в exchange сама по себе не
проверяет формат `EventEnvelope`: валидация будет в Python-коде.

Импорт применяется и к существующему тому без удаления данных. Удалённые из JSON
объекты автоматически не удаляются из RabbitMQ; несовместимое изменение свойств
существующей очереди требует отдельной миграции. Не удаляйте том ради обновления
привязок.

## Nginx / gateway (этап 1)

```bash
docker compose up -d --wait gateway
docker compose exec gateway nginx -t
curl -i http://localhost/health  # 200, тело ok
curl -i http://localhost/hello
```

Сервис `gateway` использует `infra/nginx/nginx.conf` и публикует порт 80.
Маршрут `/hello` проксируется в `http://ticket-service:8000/hello`, остальные
пути, кроме собственного `/health`, возвращают 404. Заголовки `Host`,
`X-Real-IP`, `X-Forwarded-For`, `X-Forwarded-Proto` передаются приложению.

Gateway подключён только к сети `edge`, без доступа к сетям хранилищ.
`ticket-service` слушает `0.0.0.0:8000` и подключён к `edge` и внутренней
`db-net`, где находится PostgreSQL. Порт приложения на хост не опубликован.
Gateway ждёт готовности приложения, а приложение — PostgreSQL через
`depends_on: condition: service_healthy`.

Nginx 1.28 использует `resolve` и DNS Docker (`127.0.0.11`), чтобы обновлять
адрес приложения без перезапуска. `/health` и статус `healthy` gateway показывают
готовность только Nginx, а не всей системы. Полная проверка этапа 1:

```bash
docker compose up -d --build --wait
curl http://localhost/hello  # {"message":"hello from ticket-service"}
```

При недоступном приложении `/hello` возвращает 502.

## Правила работы

- В `main` напрямую не пушим: только pull request с одобрением второго участника.
- Ветки называем `feature/<что-делаем>`, например `feature/ticket-cache`.
- Секреты (`.env`, `*.pem`) никогда не попадают в git.
