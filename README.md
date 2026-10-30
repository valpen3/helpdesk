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
и пароль из переменных окружения создаются только при первой инициализации
пустого хранилища: изменение `.env` не меняет пароль уже созданного пользователя.
`docker compose down -v` удаляет тома PostgreSQL, Redis и RabbitMQ вместе с данными.

Healthcheck проверяет доступность узла, а не доставку сообщений. Очереди,
exchange `helpdesk.events` и маршрутизация событий настраиваются на этапе 2.

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

## PostgreSQL и миграции (этап 2, участник A)

При `docker compose up -d --build --wait` задача `ticket-migrate` создаёт таблицы
`tickets` и `comments`, а затем запускается приложение. Для этой задачи статус
`Exited (0)` означает успех. Подробное объяснение и команды проверки —
в [docs/ticket-database.md](docs/ticket-database.md).

## Правила работы

- В `main` напрямую не пушим: только pull request с одобрением второго участника.
- Ветки называем `feature/<что-делаем>`, например `feature/ticket-cache`.
- Секреты (`.env`, `*.pem`) никогда не попадают в git.
