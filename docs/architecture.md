# HelpDesk — техническое задание и план разработки

26 октября 2026

## Обзор проекта

HelpDesk — система обращений в техподдержку из четырёх Python-сервисов за шлюзом Nginx. Каждый сервис владеет своими данными; между собой сервисы общаются только через события RabbitMQ, а пользователя узнают по JWT без обращения к auth-service.

Клиент создаёт тикет, агент берёт его в работу и переписывается с клиентом. notification-service рассылает письма о каждом событии, sla-service следит за сроками и эскалирует просроченные тикеты.

**Роли пользователей**

- **client** — создаёт тикеты, видит и комментирует только свои, подтверждает решение или переоткрывает тикет.
- **agent** — видит все тикеты, назначает их на себя, меняет статусы, пишет публичные и внутренние комментарии.
- **admin** — всё, что может агент, плюс управление пользователями, назначение тикетов на любого агента, удаление тикетов, статистика SLA.

**Стек**

| Слой | Технология |
| --- | --- |
| Язык и веб-фреймворк | Python 3.12, FastAPI, Uvicorn |
| Работа с БД | SQLAlchemy 2 (async) + asyncpg, миграции Alembic |
| Валидация и конфиг | Pydantic v2, pydantic-settings (всё из переменных окружения) |
| Кэш | Redis 7, клиент redis-py (redis.asyncio) |
| Брокер | RabbitMQ 3.13, клиент aio-pika |
| Аутентификация | PyJWT (RS256), пароли через argon2 (passlib/argon2-cffi) |
| Почта | aiosmtplib → Mailpit (фейковый SMTP с веб-интерфейсом) |
| Шлюз | Nginx ≥ 1.27.3 |
| Наблюдаемость | prometheus-fastapi-instrumentator, Prometheus, Grafana, JSON-логи (structlog) |
| Тесты | pytest, pytest-asyncio, httpx; нагрузка — k6 |

**Архитектурные правила, которые нужно уметь объяснить на защите**

1. Database per service: у auth, ticket и sla свои базы (`auth_db`, `tickets_db`, `sla_db`) в одном кластере PostgreSQL, у каждой свой пользователь БД без прав на чужие базы.
2. Нет синхронных вызовов сервис → сервис. Снаружи ходит только шлюз, внутри — события.
3. JWT подписывает только auth-service закрытым ключом; остальные сервисы проверяют подпись открытым ключом локально.
4. События несут все данные, нужные потребителю (email автора, заголовок и т. п.), чтобы потребителю не надо было лезть в чужую БД.

**Схема архитектуры**

```mermaid
flowchart TB
    client["Клиент: браузер, curl, k6"] --> gw["gateway · Nginx<br/>единственный открытый порт 80/443"]
    gw -->|HTTP + JWT| auth["auth-service<br/>логин, роли, JWT"]
    gw -->|HTTP + JWT| ticket["ticket-service × N<br/>тикеты, кэш, события"]
    gw -->|HTTP + JWT| sla["sla-service<br/>сроки SLA, эскалация"]
    ticket <--> mq[("RabbitMQ<br/>helpdesk.events")]
    sla <--> mq
    mq -->|notifications.q| notif["notification-service<br/>письма по событиям"]
    notif -->|SMTP| mail["Mailpit"]
    auth --> redis[("Redis<br/>кэш, лимиты, ACL")]
    ticket --> redis
    notif --> redis
    subgraph pg["PostgreSQL 16 · primary + replica"]
        adb[("auth_db")]
        tdb[("tickets_db")]
        sdb[("sla_db")]
    end
    auth --> adb
    ticket --> tdb
    sla --> sdb
```

Все запросы пользователя проходят через gateway; notification-service публичного входа не имеет и получает работу только из очереди. Каждый сервис ходит в свою базу, а RabbitMQ — единственный путь между сервисами.

## Микросервис 1: gateway (Nginx)

Единственный компонент, опубликованный наружу (порты 80/443). Своего кода нет, только конфигурация `infra/nginx/nginx.conf`. Отвечает за маршрутизацию, балансировку между репликами, ограничение частоты запросов и базовые заголовки безопасности.

**Маршрутизация**

| Префикс пути | Upstream | Примечание |
| --- | --- | --- |
| `/api/v1/auth/`, `/api/v1/users` | `auth_upstream` → auth-service:8000 | Строгий лимит на `/auth/login` и `/auth/register` |
| `/api/v1/tickets`, `/api/v1/stats` | `ticket_upstream` → ticket-service:8000 (2–N реплик) | Основная нагрузка, балансировка |
| `/api/v1/sla` | `sla_upstream` → sla-service:8000 | Только чтение статистики SLA |
| `/health` | ответ самого Nginx `200 ok` | Для внешней проверки живости |
| `/nginx_status` | `stub_status`, доступ только из сети мониторинга | Метрики для nginx-exporter |
| всё остальное | `404` | Сервисные эндпоинты `/health/*`, `/metrics` снаружи не видны |

notification-service через шлюз не доступен вообще: у него нет публичного API.

**Балансировка и отказоустойчивость**

- Upstream с `zone` и `server ticket-service:8000 resolve;` плюс `resolver 127.0.0.11 valid=10s` (DNS Docker). Так Nginx подхватывает новые реплики после `docker compose up --scale` без перезапуска.
- Алгоритм по умолчанию `least_conn`; для эксперимента в задании 6 переключается на round-robin.
- `max_fails=3 fail_timeout=10s`: реплика, трижды не ответившая, выводится из ротации на 10 секунд.
- `proxy_next_upstream error timeout http_502 http_503; proxy_next_upstream_tries 2;` — неудачный запрос повторяется на другой реплике. POST по умолчанию не повторяется, это правильно: создание тикета не идемпотентно.
- Таймауты: `proxy_connect_timeout 2s`, `proxy_read_timeout 10s`.

**Защита от перегрузки и DDoS**

| Зона | Ключ | Лимит | Где применяется |
| --- | --- | --- | --- |
| `login` | IP клиента | 5 запросов/мин, burst 3 | `/api/v1/auth/login`, `/api/v1/auth/register` |
| `api` | IP клиента | 20 запросов/с, burst 40 nodelay | все `/api/` |
| `create` | IP клиента | 10 запросов/мин, burst 5 | `POST /api/v1/tickets` |
| `conn` (`limit_conn`) | IP клиента | 20 одновременных соединений | весь сервер |

При превышении возвращается `429 Too Many Requests` (`limit_req_status 429`). Дополнительно: `client_max_body_size 64k`, `client_body_timeout 5s`, `keepalive_timeout 15s` против медленных атак типа slowloris.

**Прочее**

- Генерирует `X-Request-ID` (`$request_id`), если его нет, и передаёт сервисам. Сервисы пишут его в каждую строку лога, так один запрос можно проследить через все логи.
- Заголовки: `server_tokens off`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`.
- TLS (по желанию): самоподписанный сертификат из `scripts/gen_certs.sh`, редирект 80 → 443.
- Лог доступа в JSON с полями `request_id`, `upstream_addr`, `upstream_response_time`, `status`. По `upstream_addr` на защите видно, что запросы распределяются по разным репликам.

## Микросервис 2: auth-service

Отвечает за пользователей, пароли, роли и выдачу токенов. Единственный сервис, у которого есть закрытый ключ JWT. С брокером не работает.

**Хранилища:** PostgreSQL `auth_db` (пользователь БД `auth_user`), Redis с ACL-пользователем `auth-svc` (доступ только к ключам `auth:*`).

**Модель данных (`auth_db`)**

| Таблица | Поля | Назначение |
| --- | --- | --- |
| `users` | `id UUID PK`, `email VARCHAR UNIQUE`, `password_hash`, `full_name`, `role ENUM(client, agent, admin)`, `is_active BOOL`, `created_at`, `updated_at` | Учётные записи |
| `refresh_tokens` | `jti UUID PK`, `user_id FK → users`, `expires_at`, `revoked_at NULL`, `created_at`, `user_agent` | Выданные refresh-токены и их отзыв |

При старте, если в БД нет ни одного админа, создаётся админ из переменных `ADMIN_EMAIL` / `ADMIN_PASSWORD` (seed).

**Токены**

- Access token: JWT RS256, живёт 15 минут. Claims: `sub` (id), `email`, `name`, `role`, `jti`, `iat`, `exp`, `iss=helpdesk-auth`.
- Refresh token: JWT с `type=refresh`, живёт 7 дней, его `jti` хранится в `refresh_tokens`. При каждом обновлении старый отзывается, выдаётся новый (rotation). Повторное использование отозванного токена отзывает все токены пользователя: признак кражи.
- Открытый ключ кладётся в другие сервисы как docker secret `jwt_public.pem`, закрытый — только в auth-service (`jwt_private.pem`). Генерация: `scripts/gen_keys.sh` (openssl).
- Logout отзывает refresh-токен. Уже выданный access-токен доживает свои 15 минут: это сознательный компромисс, чтобы не делать сетевой запрос к auth на каждый вызов.

**Эндпоинты**

| Метод и путь | Доступ | Тело запроса | Ответ | Логика |
| --- | --- | --- | --- | --- |
| `POST /api/v1/auth/register` | все | `{email, password, full_name}` | `201 {id, email, full_name, role}` | Пароль ≥ 8 символов; роль всегда `client`; дубль email → `409` |
| `POST /api/v1/auth/login` | все | `{email, password}` | `200 {access_token, refresh_token, token_type, expires_in}` | Проверка защиты от перебора (ниже); неверная пара → `401` с одинаковым текстом для «нет пользователя» и «неверный пароль» |
| `POST /api/v1/auth/refresh` | все | `{refresh_token}` | `200` новая пара токенов | Rotation, проверка отзыва |
| `POST /api/v1/auth/logout` | авторизованный | `{refresh_token}` | `204` | Отзыв refresh-токена |
| `GET /api/v1/auth/me` | авторизованный | — | `200` профиль | Cache-aside: `auth:user:{id}`, TTL 300 с |
| `GET /api/v1/users?role=&page=&size=` | admin | — | `200 {items, total}` | Список с фильтром по роли |
| `GET /api/v1/users/{id}` | admin | — | `200` профиль | — |
| `PATCH /api/v1/users/{id}` | admin | `{role?, is_active?, full_name?}` | `200` профиль | Удаляет `auth:user:{id}` из кэша; блокировка (`is_active=false`) отзывает все refresh-токены |
| `GET /health/live` | внутренний | — | `200` | Процесс жив |
| `GET /health/ready` | внутренний | — | `200` / `503` | Проверка соединения с БД и Redis |
| `GET /metrics` | только Prometheus | — | метрики | prometheus-fastapi-instrumentator |

**Защита от перебора паролей (Redis)**

- `auth:login_fail:{email}` — счётчик неудачных попыток, `INCR` + `EXPIRE 900`.
- После 5 неудачных попыток вход для этого email блокируется на 15 минут: ответ `429` с `Retry-After`. Успешный вход удаляет счётчик.
- Это второй уровень после лимита Nginx: Nginx режет по IP, сервис — по аккаунту (атака с многих IP).

**Метрики, которые стоит добавить вручную:** `auth_login_total{result="success|fail|locked"}`.

**Тесты:** регистрация, логин, неверный пароль, блокировка после 5 попыток, rotation и повторное использование refresh-токена, доступ к `/users` не-админом → `403`.

## Микросервис 3: ticket-service

Ядро системы: тикеты, комментарии, статусы, назначения. Именно на нём выполняется минимальный сценарий задания 3: запись в БД → инвалидация кэша → событие в брокер, чтение через кэш. Работает в 2–N репликах за Nginx, поэтому не хранит состояние в памяти.

**Хранилища:** PostgreSQL `tickets_db` (пользователь `tickets_user`), Redis с ACL-пользователем `ticket-svc` (ключи `ticket:*`, `tickets:*`, `stats:*`, `rate:*`), RabbitMQ с пользователем `ticket-svc` (публикация в exchange `helpdesk.events`, чтение очереди `ticket.sla.q`).

**Аутентификация:** зависимость FastAPI `get_current_user` читает заголовок `Authorization: Bearer`, проверяет подпись открытым ключом, `exp` и `iss`, и возвращает `{id, email, name, role}`. Нет токена или он невалиден → `401`, роль не подходит → `403`.

**Модель данных (`tickets_db`)**

| Таблица | Поля | Назначение |
| --- | --- | --- |
| `tickets` | `id BIGSERIAL PK`, `title VARCHAR(200)`, `description TEXT`, `status ENUM`, `priority ENUM(low, medium, high, critical)`, `author_id UUID`, `author_email`, `assignee_id UUID NULL`, `assignee_email NULL`, `escalated BOOL DEFAULT false`, `version INT`, `created_at`, `updated_at`, `resolved_at NULL`, `closed_at NULL` | Тикеты; индексы по `status`, `author_id`, `assignee_id` |
| `comments` | `id BIGSERIAL PK`, `ticket_id FK`, `author_id`, `author_email`, `author_role`, `body TEXT`, `is_internal BOOL`, `created_at` | Переписка; внутренние комментарии видят только агенты |
| `ticket_history` | `id`, `ticket_id FK`, `actor_id`, `field`, `old_value`, `new_value`, `created_at` | Аудит всех изменений |
| `outbox` | `id UUID PK`, `routing_key`, `payload JSONB`, `created_at`, `published_at NULL`, `attempts INT` | Transactional outbox (этап надёжности) |
| `processed_events` | `event_id UUID PK`, `processed_at` | Идемпотентность собственного consumer'а |

`version` используется для оптимистичной блокировки: `UPDATE ... WHERE id = :id AND version = :v`. Если два агента одновременно меняют тикет, второй получит `409 Conflict`.

**Жизненный цикл тикета**

| Из статуса | В статус | Кто может | Как происходит |
| --- | --- | --- | --- |
| `open` | `in_progress` | agent, admin | Автоматически при назначении (`/assign`) |
| `open` | `closed` | автор | Клиент отменил обращение |
| `in_progress` | `waiting_client` | agent, admin | Агент задал вопрос клиенту |
| `waiting_client` | `in_progress` | автор, agent | Автоматически при комментарии автора |
| `in_progress` | `resolved` | agent, admin | Решено; ставится `resolved_at` |
| `resolved` | `closed` | автор, admin | Клиент подтвердил решение; ставится `closed_at` |
| `resolved` | `in_progress` | автор | Клиент переоткрыл тикет |

```mermaid
stateDiagram-v2
    [*] --> open
    open --> in_progress: назначение агента
    open --> closed: клиент отменил
    in_progress --> waiting_client: вопрос клиенту
    waiting_client --> in_progress: ответ клиента
    in_progress --> resolved: решено
    resolved --> closed: клиент подтвердил
    resolved --> in_progress: клиент переоткрыл
    closed --> [*]
```

Любой другой переход → `422` с текстом «переход X → Y запрещён». Матрица переходов лежит в одном словаре `ALLOWED_TRANSITIONS` в `app/domain/status.py`, её удобно показывать на защите.

**Эндпоинты**

| Метод и путь | Доступ | Тело / параметры | Ответ | Логика |
| --- | --- | --- | --- | --- |
| `GET /hello` | все | — | `{"message": "hello from ticket-service"}` | Заглушка задания 2 |
| `POST /api/v1/tickets` | client | `{title, description, priority}` | `201` тикет | INSERT + запись в history → инвалидация списков и счётчиков → событие `ticket.created`. Лимит 20 тикетов в час на пользователя (`rate:create:{user_id}`) |
| `GET /api/v1/tickets/{id}` | автор, agent, admin | — | `200` тикет, заголовок `X-Cache: HIT/MISS` | Cache-aside `ticket:{id}`, TTL 300 с. Проверка доступа после чтения из кэша. Чужой тикет для client → `404` (не выдаём сам факт существования) |
| `GET /api/v1/tickets` | все роли | `status, priority, assignee=me, escalated, page, size ≤ 100` | `200 {items, total, page, size}` | client видит только свои. Кэш `tickets:list:v{N}:{hash параметров и роли}`, TTL 60 с |
| `PATCH /api/v1/tickets/{id}` | автор (только в `open`), agent, admin | `{title?, description?, priority?, version}` | `200` тикет | Оптимистичная блокировка → `DEL ticket:{id}` + `INCR tickets:list_version` → `ticket.updated` |
| `POST /api/v1/tickets/{id}/status` | по матрице | `{status, comment?, version}` | `200` тикет | Проверка перехода → UPDATE + history → инвалидация → `ticket.status_changed {old_status, new_status}` |
| `POST /api/v1/tickets/{id}/assign` | agent (на себя), admin (на любого) | `{assignee_id?, assignee_email?}` | `200` тикет | Пустое тело = на себя. Статус `open` → `in_progress` → `ticket.assigned` |
| `POST /api/v1/tickets/{id}/comments` | автор, agent, admin | `{body, is_internal=false}` | `201` комментарий | client не может ставить `is_internal`. Комментарий автора в `waiting_client` переводит в `in_progress`. → `DEL ticket:{id}:comments` → `ticket.commented` |
| `GET /api/v1/tickets/{id}/comments` | автор, agent, admin | — | `200` список | Кэш `ticket:{id}:comments`, TTL 300 с. Для client внутренние комментарии отфильтровываются |
| `GET /api/v1/tickets/{id}/history` | agent, admin | — | `200` список изменений | Без кэша |
| `DELETE /api/v1/tickets/{id}` | admin | — | `204` | Удаление (нужно для демонстрации восстановления из бэкапа) → `ticket.deleted` |
| `GET /api/v1/stats` | agent, admin | — | `200 {open, in_progress, waiting_client, resolved, closed, escalated}` | Redis hash `stats:status`, при отсутствии пересчитывается из БД (`GROUP BY status`) и кладётся обратно |
| `GET /health/live`, `/health/ready`, `/metrics` | внутренние | — | — | ready проверяет БД, Redis и соединение с RabbitMQ |

**Кэш: какие ключи и когда сбрасываются**

| Ключ | Тип, TTL | Записывается | Сбрасывается |
| --- | --- | --- | --- |
| `ticket:{id}` | string (JSON), 300 с | при промахе на `GET /tickets/{id}` | любое изменение тикета, эскалация, удаление |
| `ticket:{id}:comments` | string (JSON), 300 с | при промахе на `GET .../comments` | новый комментарий |
| `tickets:list_version` | integer, без TTL | — | `INCR` при любом изменении любого тикета |
| `tickets:list:v{N}:{hash}` | string (JSON), 60 с | при промахе на `GET /tickets` | не удаляется явно: после `INCR` версии старые ключи просто не читаются и истекают по TTL |
| `stats:status` | hash | `HINCRBY` при создании и смене статуса | пересчёт, если ключа нет |
| `rate:create:{user_id}` | integer, 3600 с | `INCR` при создании тикета | истекает |

Версионирование списков — главный приём, который стоит объяснить: удалять все ключи списков по шаблону (`SCAN` + `DEL`) медленно, а `INCR` одной версии мгновенно делает все старые списки невидимыми.

Если Redis недоступен, сервис не падает: все операции с кэшем обёрнуты в `try/except`, при ошибке чтение идёт сразу в БД, а в лог пишется предупреждение (graceful degradation).

**Публикуемые события** (exchange `helpdesk.events`, тип topic)

| Routing key | Когда | Данные в `data` |
| --- | --- | --- |
| `ticket.created` | создан тикет | `ticket_id, title, priority, author_id, author_email, created_at` |
| `ticket.updated` | изменены поля | `ticket_id, title, changed_fields, author_email` |
| `ticket.status_changed` | смена статуса | `ticket_id, title, old_status, new_status, author_email, assignee_email, actor_role` |
| `ticket.assigned` | назначен исполнитель | `ticket_id, title, assignee_id, assignee_email, author_email` |
| `ticket.commented` | новый комментарий | `ticket_id, title, comment_id, is_internal, actor_role, author_email, assignee_email` |
| `ticket.deleted` | тикет удалён | `ticket_id` |

Конверт каждого события одинаковый: `{event_id: UUID, event_type, occurred_at, producer: "ticket-service", request_id, data}`. Сообщения публикуются как persistent (`delivery_mode=2`) с `message_id = event_id` и publisher confirms.

На этапе задания 3 событие публикуется сразу после `COMMIT`. На этапе надёжности вводится transactional outbox: событие пишется в таблицу `outbox` в той же транзакции, что и тикет, а фоновая задача раз в секунду публикует неотправленные записи. Так событие не теряется, если RabbitMQ лежал в момент записи.

**Потребляемые события:** очередь `ticket.sla.q` (ключ `ticket.sla_breached`). При получении сервис ставит `escalated = true`, при `kind = response` повышает приоритет на ступень, пишет history, удаляет `ticket:{id}` и увеличивает версию списков. Повтор того же `event_id` пропускается через таблицу `processed_events`.

**Метрики:** `tickets_created_total`, `ticket_cache_requests_total{result="hit|miss"}`, `events_published_total{routing_key}`, `outbox_pending` (gauge). Hit ratio кэша из этих метрик — хорошая цифра для защиты.

**Тесты:** создание и чтение (MISS, затем HIT), инвалидация после изменения, запрещённый переход статуса, client не видит чужой тикет и внутренние комментарии, конфликт версий, работа при выключенном Redis.

## Микросервис 4: notification-service

Чистый consumer: читает события из RabbitMQ и отправляет письма через SMTP в Mailpit. Публичного API нет, к PostgreSQL доступа нет: всё нужное для письма приходит внутри события. Масштабируется числом воркеров (реплик), которые читают одну и ту же очередь.

**Хранилища:** RabbitMQ с пользователем `notif-svc` (право только читать `notifications.q` и публиковать в retry-exchange), Redis с ACL-пользователем `notif-svc` (ключи `notif:*`), SMTP Mailpit:1025.

**Устройство процесса**

1. При старте подключается к RabbitMQ (`aio_pika.connect_robust` — сам переподключается после обрыва) и ставит `prefetch_count=10`: воркер берёт не больше 10 неподтверждённых сообщений.
2. Для каждого сообщения: парсит конверт → проверяет идемпотентность → определяет получателей и шаблон → отправляет письмо → `ack`.
3. Параллельно поднимает маленький HTTP-сервер (FastAPI) только для `/health/live`, `/health/ready` и `/metrics`. Порт доступен только из сети мониторинга.

**Правила рассылки**

| Событие | Получатель | Тема письма |
| --- | --- | --- |
| `ticket.created` | автор | «Обращение #{id} принято» |
| `ticket.created` с `priority = critical` | дежурный (`ONCALL_EMAIL` из env) | «Критичное обращение #{id}» |
| `ticket.assigned` | автор и назначенный агент | «Обращение #{id} взято в работу» |
| `ticket.status_changed` | автор (если менял не он) | «Статус обращения #{id}: {new_status}» |
| `ticket.commented` (не internal) | автор или агент, в зависимости от того, кто написал | «Новый комментарий к #{id}» |
| `ticket.commented` (internal) | никому | — |
| `ticket.sla_breached` | админ (`ADMIN_NOTIFY_EMAIL`) и назначенный агент | «Нарушен SLA по #{id}» |

Шаблоны писем — Jinja2-файлы в `app/templates/`, по одному на событие.

**Идемпотентность.** RabbitMQ гарантирует доставку «хотя бы один раз», поэтому одно сообщение может прийти дважды (например, воркер упал после отправки письма, но до `ack`). Перед обработкой выполняется `SET notif:done:{event_id} 1 NX EX 604800`. Если ключ уже был, сообщение подтверждается без отправки письма.

**Обработка ошибок и повторы**

- Ошибка разбора JSON или неизвестный тип события → `reject(requeue=False)`, сообщение сразу уходит в `notifications.dlq`: повтор не поможет.
- Ошибка SMTP или таймаут → сообщение публикуется в `notifications.retry` (очередь с `x-message-ttl=30000` и dead-letter обратно в основную очередь), исходное подтверждается. Номер попытки хранится в заголовке `x-attempt`.
- После 3 неудачных попыток сообщение отправляется в `notifications.dlq` и пишется ошибка в лог. Ключ идемпотентности при ошибке удаляется, чтобы повтор смог отправить письмо.

**Журнал отправок:** после успешной отправки `LPUSH notif:recent` с JSON `{event_id, to, subject, sent_at}` и `LTRIM 0 99`. Удобно для отладки (`redis-cli LRANGE notif:recent 0 10`).

**Метрики:** `notifications_sent_total{event_type}`, `notifications_failed_total{reason}`, `notifications_duplicates_total`, гистограмма `notification_delay_seconds` (разница между `occurred_at` события и моментом отправки) — это SLI «задержка уведомлений».

**Тесты:** юнит-тесты правил рассылки (событие → получатели), дубль события не отправляет второе письмо, при недоступном SMTP сообщение уходит в retry и затем в DLQ.

## Микросервис 5: sla-service

Следит за сроками реакции и решения. Одновременно consumer (узнаёт о тикетах из событий), планировщик (раз в 30 секунд ищет просрочки) и producer (публикует `ticket.sla_breached`). Плюс небольшое API для статистики. На нём показывается цепочка из нескольких сервисов через брокер.

**Хранилища:** PostgreSQL `sla_db` (пользователь `sla_user`), RabbitMQ с пользователем `sla-svc` (читает `sla.q`, публикует в `helpdesk.events` только `ticket.sla_breached`). Redis не нужен.

**Политики SLA**

| Приоритет | Время первой реакции | Время решения |
| --- | --- | --- |
| critical | 15 мин | 4 ч |
| high | 1 ч | 8 ч |
| medium | 4 ч | 24 ч |
| low | 8 ч | 72 ч |

Политики лежат в таблице `sla_policies` и заполняются миграцией. Для демонстрации есть переменная `SLA_TIME_SCALE`: при значении `60` минуты превращаются в секунды, и critical-тикет нарушает SLA реакции через 15 секунд.

«Реакция» = тикет назначен на агента или переведён в `in_progress`. «Решение» = статус `resolved`. В статусе `waiting_client` таймер решения ставится на паузу: ожидание ответа клиента не вина поддержки.

**Модель данных (`sla_db`)**

| Таблица | Поля | Назначение |
| --- | --- | --- |
| `sla_policies` | `priority PK`, `response_minutes`, `resolution_minutes` | Нормативы |
| `sla_records` | `ticket_id PK`, `title`, `priority`, `created_at`, `response_due`, `resolution_due`, `responded_at NULL`, `resolved_at NULL`, `paused_at NULL`, `paused_total_sec`, `response_breached BOOL`, `resolution_breached BOOL`, `closed BOOL` | Состояние SLA по каждому тикету |
| `processed_events` | `event_id PK`, `processed_at` | Идемпотентность |

**Обработка событий (очередь `sla.q`)**

| Событие | Действие |
| --- | --- |
| `ticket.created` | Создать `sla_records`: дедлайны = `created_at` + норматив по приоритету |
| `ticket.assigned` | Заполнить `responded_at`, если пусто |
| `ticket.status_changed` → `in_progress` | Заполнить `responded_at`; если тикет был на паузе — снять паузу и сдвинуть `resolution_due` на длительность паузы |
| `ticket.status_changed` → `waiting_client` | Поставить паузу (`paused_at = now`) |
| `ticket.status_changed` → `resolved` | Заполнить `resolved_at` |
| `ticket.status_changed` → `closed`, `ticket.deleted` | `closed = true`, тикет больше не проверяется |
| `ticket.updated` с изменением приоритета | Пересчитать дедлайны, ещё не наступившие |

Запись в `sla_records` и в `processed_events` делается в одной транзакции, после неё `ack`. Повторное событие с тем же `event_id` пропускается.

**Планировщик.** Фоновая asyncio-задача раз в `SLA_CHECK_INTERVAL` (30 с) выполняет:

```sql
SELECT * FROM sla_records
WHERE closed = false
  AND ((responded_at IS NULL AND response_breached = false AND response_due < now())
    OR (resolved_at IS NULL AND paused_at IS NULL AND resolution_breached = false AND resolution_due < now()))
FOR UPDATE SKIP LOCKED
LIMIT 100;
```

Для каждой найденной записи ставится флаг нарушения и публикуется `ticket.sla_breached` с данными `{ticket_id, title, kind: "response" | "resolution", due_at, priority}`. `FOR UPDATE SKIP LOCKED` позволяет запустить несколько реплик sla-service: каждая заберёт свои строки, и одно нарушение не будет опубликовано дважды. Это стоит объяснить на защите.

Событие `ticket.sla_breached` получают два потребителя: notification-service (письмо админу) и ticket-service (флаг `escalated` и повышение приоритета).

**Эндпоинты** (через шлюз, проверка JWT как в ticket-service)

| Метод и путь | Доступ | Ответ | Логика |
| --- | --- | --- | --- |
| `GET /api/v1/sla/policies` | agent, admin | список нормативов | Чтение `sla_policies` |
| `GET /api/v1/sla/tickets/{ticket_id}` | agent, admin | `{response_due, resolution_due, responded_at, resolved_at, response_breached, resolution_breached, time_left_sec}` | Состояние SLA по тикету |
| `GET /api/v1/sla/breaches?from=&to=&kind=` | admin | список нарушений | Для отчёта |
| `GET /api/v1/sla/stats?period=7d` | admin | `{total, response_met_pct, resolution_met_pct, breaches_by_priority}` | Доля тикетов, уложившихся в SLA |
| `GET /health/live`, `/health/ready`, `/metrics` | внутренние | — | ready проверяет БД и брокер |

**Метрики:** `sla_breaches_total{kind, priority}`, `sla_check_duration_seconds`, `sla_open_records` (gauge).

**Тесты:** расчёт дедлайнов по приоритету, пауза и снятие паузы, планировщик находит просрочку и публикует событие ровно один раз при двух параллельных проверках.

## Инфраструктурные компоненты

Всё поднимается одним `docker compose up -d`. Базовый файл `docker-compose.yml` содержит одну копию каждого компонента; `docker-compose.ha.yml` (этап надёжности) добавляет реплики и кластеры поверх него: `docker compose -f docker-compose.yml -f docker-compose.ha.yml up -d`. Пароли и ключи — в `.env` (в git только `.env.example`) и docker secrets.

### PostgreSQL 16

- Один кластер, три базы: `auth_db`, `tickets_db`, `sla_db`. Скрипт `infra/postgres/init/01-init.sql` создаёт пользователей `auth_user`, `tickets_user`, `sla_user`, у каждого права только на свою базу; `REVOKE CONNECT ... FROM PUBLIC` закрывает чужие базы.
- `pg_hba.conf` пускает каждого пользователя только к своей базе и только с IP своего сервиса: `host tickets_db tickets_user 10.10.2.0/24 scram-sha-256`. Всё остальное `reject`.
- Миграции Alembic запускаются отдельными одноразовыми контейнерами (`auth-migrate`, `ticket-migrate`, `sla-migrate`), сервисы стартуют после них через `depends_on: condition: service_completed_successfully`. Так несколько реплик не запускают миграции одновременно.
- Этап надёжности: `postgres-primary` + `postgres-replica` (streaming replication). Реплика при первом старте делает `pg_basebackup -R` с primary. Строка подключения сервисов перечисляет оба хоста: `postgresql+asyncpg://tickets_user:***@postgres-primary:5432,postgres-replica:5432/tickets_db?target_session_attrs=read-write`. После `scripts/failover.sh` (выполняет `pg_ctl promote` на реплике) сервисы при переподключении сами находят новый primary.
- Healthcheck: `pg_isready -U postgres`.

### Redis 7

- Конфиг: `appendonly yes`, `appendfsync everysec`, `maxmemory 256mb`, `maxmemory-policy volatile-lru` (вытесняются только ключи с TTL, счётчики версий и статистики не пропадут).
- ACL-файл `infra/redis/users.acl`: пользователь `default` отключён; каждый сервис получает своего пользователя с доступом только к своим префиксам и без опасных команд:

```
user default off
user auth-svc on >${AUTH_REDIS_PASSWORD} ~auth:* +@all -@dangerous
user ticket-svc on >${TICKET_REDIS_PASSWORD} ~ticket:* ~tickets:* ~stats:* ~rate:* +@all -@dangerous
user notif-svc on >${NOTIF_REDIS_PASSWORD} ~notif:* +@all -@dangerous
user exporter on >${EXPORTER_REDIS_PASSWORD} +info +ping +config|get +client|list
```

- Попытка notification-service прочитать `ticket:42` вернёт `NOPERM` — удобная демонстрация изоляции внутри одного Redis.
- Этап надёжности: `redis-master`, `redis-replica` и три `redis-sentinel` (кворум 2). Сервисы подключаются через `redis.asyncio.sentinel.Sentinel` по имени мастера `helpdesk`, переключение автоматическое примерно за 10–15 секунд.
- Healthcheck: `redis-cli --user ... ping`.

### RabbitMQ 3.13 (management + prometheus)

Топология загружается при старте из `infra/rabbitmq/definitions.json` (`load_definitions` в `rabbitmq.conf`), сервисы её не создают (у них нет права configure).

| Exchange | Тип | Назначение |
| --- | --- | --- |
| `helpdesk.events` | topic | Все доменные события |
| `helpdesk.retry` | direct | Отложенные повторы уведомлений |
| `helpdesk.dlx` | direct | Dead letter: сообщения, которые не удалось обработать |

| Очередь | Тип | Привязки | Аргументы |
| --- | --- | --- | --- |
| `notifications.q` | quorum | `ticket.created`, `ticket.assigned`, `ticket.status_changed`, `ticket.commented`, `ticket.sla_breached` | DLX `helpdesk.dlx`, ключ `notifications` |
| `notifications.retry` | quorum | `helpdesk.retry` / `notifications` | `x-message-ttl 30000`, DLX `""` с ключом `notifications.q` (возврат в основную очередь через 30 с) |
| `sla.q` | quorum | `ticket.created`, `ticket.assigned`, `ticket.status_changed`, `ticket.updated`, `ticket.deleted` | DLX → `sla.dlq`, `x-delivery-limit 5` |
| `ticket.sla.q` | quorum | `ticket.sla_breached` | DLX → `ticket.sla.dlq`, `x-delivery-limit 5` |
| `notifications.dlq`, `sla.dlq`, `ticket.sla.dlq` | quorum | `helpdesk.dlx` | — |

`x-delivery-limit` — встроенная защита quorum-очередей от «ядовитых» сообщений: после 5 неудачных доставок сообщение уходит в DLQ, а не крутится бесконечно.

| Пользователь | configure | write | read | Права на routing key (topic permissions) |
| --- | --- | --- | --- | --- |
| `ticket-svc` | нет | `helpdesk.events` | `ticket.sla.q` | публикует только `ticket.(created\|updated\|status_changed\|assigned\|commented\|deleted)` |
| `notif-svc` | нет | `helpdesk.retry`, `helpdesk.dlx` | `notifications.q` | — |
| `sla-svc` | нет | `helpdesk.events` | `sla.q` | публикует только `ticket.sla_breached` |
| `admin` | всё | всё | всё | только для веб-консоли |

Этап надёжности: кластер из трёх узлов `rabbitmq-1..3` (общий erlang cookie, peer discovery `classic_config`). Quorum-очереди реплицируются на все три узла и переживают потерю одного. Сервисы получают список узлов и подключаются к первому доступному. Healthcheck: `rabbitmq-diagnostics -q ping`.

### Mailpit

Образ `axllent/mailpit`: SMTP на 1025 (принимает любые письма и никуда не отправляет), веб-интерфейс на 8025, опубликован только на `127.0.0.1:8025`. Используется вместо заброшенного MailHog.

### Резервное копирование (контейнер `pg-backup`)

- Образ `postgres:16` + скрипт `infra/backup/backup.sh`, запускается по расписанию (цикл с `sleep` или supercronic) каждые 6 часов.
- Для каждой базы: `pg_dump -Fc` с реплики (не нагружает primary) в `/backups/{db}/{db}_{YYYYmmdd_HHMM}.dump`, затем проверка `pg_restore --list` (файл читается), удаление копий старше 7 дней.
- Каталог `/backups` смонтирован на хост (`./backups`); по желанию дополнительно копия в MinIO как «удалённое хранилище».
- `scripts/restore.sh <db> <файл>`: останавливает сервис базы, пересоздаёт базу, выполняет `pg_restore`, запускает сервис.
- Опционально: архивация WAL (`archive_mode=on`, WAL-G) для восстановления на момент времени и RPO в минутах.

### Мониторинг

- Prometheus собирает `/metrics` всех сервисов, а также postgres-exporter, redis_exporter, встроенный плагин RabbitMQ (порт 15692) и nginx-prometheus-exporter.
- Grafana (только `127.0.0.1:3000`) с заранее подготовленными дашбордами: RED-метрики по каждому сервису (запросы, ошибки, задержка), hit ratio кэша, глубина очередей, SLI из раздела надёжности.
- Логи всех сервисов — JSON в stdout с полями `service`, `level`, `request_id`, `event_id`; смотрятся через `docker compose logs`.

## Сети и firewall (задание 4)

Сеть делится на семь docker-сетей по принципу «каждый видит только то, что ему нужно». Сети с данными помечены `internal: true`: из них нет выхода в интернет, и туда нельзя пробросить порт. Второй уровень защиты — правила iptables в цепочке `DOCKER-USER` и права на уровне самих хранилищ (`pg_hba`, Redis ACL, права RabbitMQ).

**Сети**

| Сеть | Подсеть | internal | Участники |
| --- | --- | --- | --- |
| `edge` | 10.10.1.0/24 | нет | gateway, auth, ticket, sla |
| `db-net` | 10.10.2.0/24 | да | postgres-primary, postgres-replica, auth, ticket, sla, *-migrate, pg-backup, postgres-exporter |
| `cache-net` | 10.10.3.0/24 | да | redis (+ sentinel), auth, ticket, notification, redis-exporter |
| `broker-net` | 10.10.4.0/24 | да | rabbitmq, ticket, notification, sla |
| `mail-net` | 10.10.5.0/24 | да | notification, mailpit |
| `mon-net` | 10.10.6.0/24 | да | prometheus (10.10.6.10), все сервисы и экспортёры, gateway, rabbitmq |
| `admin-net` | 10.10.9.0/24 | нет | grafana, mailpit, rabbitmq (консоль); порты только на 127.0.0.1 |

Наружу опубликованы только порты gateway (80/443). Grafana, консоль RabbitMQ и Mailpit доступны лишь с самой машины.

**Явно разрешённые потоки**

| Откуда | Куда | Порт | Зачем |
| --- | --- | --- | --- |
| интернет | gateway | 80/443 | Вход пользователей |
| gateway | auth, ticket, sla | 8000 | Проксирование API |
| auth, ticket, sla | postgres-primary/replica | 5432 | Своя база |
| auth, ticket, notification | redis | 6379 | Кэш, лимиты, идемпотентность |
| ticket, notification, sla | rabbitmq | 5672 | События |
| notification | mailpit | 1025 | Отправка писем |
| prometheus | все сервисы и экспортёры | 8000, 9xxx, 15692 | Сбор метрик |
| pg-backup | postgres-replica | 5432 | Резервные копии |

**Явно запрещённые потоки и как это проверить**

| Откуда | Куда | Почему запрещено | Чем обеспечено | Проверка |
| --- | --- | --- | --- | --- |
| gateway | postgres, redis, rabbitmq | Шлюз не должен видеть данные | Нет общей сети | `docker compose exec gateway nc -zv -w2 postgres-primary 5432` → ошибка разрешения имени |
| notification | postgres | Минимальные привилегии: данные приходят в событии | Нет общей сети | `nc -zv postgres-primary 5432` из notification → ошибка |
| auth | rabbitmq | Auth не участвует в событиях | Нет общей сети | `nc -zv rabbitmq 5672` из auth → ошибка |
| sla | redis | Не нужен | Нет общей сети | `nc -zv redis 6379` из sla → ошибка |
| notification | auth, ticket, sla по HTTP | Нет синхронных вызовов между сервисами | Нет общей сети | `nc -zv ticket-service 8000` из notification → ошибка |
| gateway | notification | У notification нет публичного API | Нет общей сети | `nc -zv notification-service 8000` из gateway → ошибка |
| любой, кроме prometheus | любой в `mon-net` | Сеть мониторинга не должна стать обходным путём | iptables `DOCKER-USER` | из notification `nc -zv 10.10.6.x 8000` (адрес ticket в mon-net) → таймаут |
| notification | `ticket:*` в Redis | Чужие ключи | Redis ACL | `redis-cli --user notif-svc GET ticket:1` → `NOPERM` |
| sla-svc | публикация `ticket.created` | Подделка чужих событий | topic permissions RabbitMQ | публикация из sla с чужим ключом → `ACCESS_REFUSED` |
| ticket | `auth_db` | Чужая база | `REVOKE CONNECT`, `pg_hba` | `psql -U tickets_user -d auth_db` → отказ |
| notification | интернет | Защита от утечки данных | `internal: true` | `curl -m3 https://example.com` из notification → ошибка |

**Зачем нужен iptables, если есть сети.** Сеть `mon-net` объединяет почти все сервисы ради Prometheus, и без дополнительных правил через неё notification мог бы достучаться до ticket. Правила в `infra/firewall/iptables.sh` закрывают эту дыру:

```bash
# Разрешаем ответы на уже установленные соединения
iptables -I DOCKER-USER -s 10.10.6.0/24 -d 10.10.6.0/24 -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
# Prometheus может ходить ко всем в mon-net
iptables -I DOCKER-USER 2 -s 10.10.6.10 -d 10.10.6.0/24 -j ACCEPT
# Всё остальное внутри mon-net запрещено и логируется
iptables -I DOCKER-USER 3 -s 10.10.6.0/24 -d 10.10.6.0/24 -j LOG --log-prefix "MON-NET-DROP "
iptables -I DOCKER-USER 4 -s 10.10.6.0/24 -d 10.10.6.0/24 -j DROP
```

Для этого Prometheus получает фиксированный адрес `ipv4_address: 10.10.6.10`, а на хосте должно быть `net.bridge.bridge-nf-call-iptables=1` (иначе трафик внутри одного моста не проходит через iptables). Правила работают только на Linux-хосте: на Docker Desktop для macOS и Windows iptables живут внутри виртуальной машины, поэтому демонстрацию лучше проводить на Linux-ВМ.

**Автоматическая проверка.** Скрипт `scripts/check_network.sh` проходит по таблице ожидаемых разрешений и запретов, для каждой пары выполняет `docker compose exec <src> nc -zv -w2 <dst> <port>` и печатает `OK ALLOW`, `OK DENY` или `FAIL`. На защите достаточно запустить его одной командой. Для этого во все Python-образы ставится `netcat-openbsd`.

## Надёжность, балансировка и нагрузка (задания 5 и 6)

Целевая доступность API — 99.5% за 30 дней, это 3 ч 36 мин допустимого простоя в месяц (error budget). Все механизмы отказоустойчивости ниже подобраны так, чтобы отказ любого одного компонента укладывался в этот бюджет с большим запасом. В задании параметры названы «CLI»; по смыслу это SLI (Service Level Indicator), так и описываем, но на защите стоит уточнить у преподавателя.

### SLI и SLO

| SLI | Как считается | SLO |
| --- | --- | --- |
| Доступность API | доля ответов шлюза не 5xx от всех ответов | ≥ 99.5% за 30 дней |
| Задержка чтения | p95 `GET /api/v1/tickets/{id}` | < 200 мс |
| Задержка записи | p95 `POST /api/v1/tickets` | < 500 мс |
| Задержка уведомлений | p95 `notification_delay_seconds` | < 60 с |
| Обнаружение нарушения SLA | время от дедлайна до события `ticket.sla_breached` | < 60 с (проверка раз в 30 с) |
| Потеря событий | подтверждённые события, не дошедшие до потребителя | 0 |

Все SLI выводятся на отдельный дашборд Grafana «SLO».

### MTBF и MTTR

MTBF (Mean Time Between Failures) — среднее время работы между отказами. MTTR (Mean Time To Recovery) — среднее время от отказа до восстановления. Доступность выражается через них:

$$A = \frac{MTBF}{MTBF + MTTR}$$

Цели системы: MTBF ≥ 720 ч (не более одного инцидента в месяц), MTTR ≤ 15 мин. При таких значениях A = 720 / 720,25 ≈ 99.97%, что выше SLO 99.5%.

| Компонент | Отказ | Как обнаруживается | Как восстанавливается | Целевой MTTR | Влияние на пользователя |
| --- | --- | --- | --- | --- | --- |
| ticket-service (реплика) | падение процесса | healthcheck, `max_fails` в Nginx | `restart: unless-stopped`, Nginx уводит трафик на другие реплики | 20 с на реплику, 0 для пользователя | нет |
| auth-service | падение процесса | healthcheck | 2 реплики + рестарт | 20 с | нет (выданные токены продолжают работать) |
| notification-service | падение процесса | healthcheck, рост очереди | рестарт, сообщения ждут в очереди | 30 с | письма приходят с задержкой |
| sla-service | падение процесса | healthcheck | рестарт; `SKIP LOCKED` при нескольких репликах | 30 с | эскалация с задержкой |
| PostgreSQL primary | отказ узла | алерт `pg_up == 0` | `scripts/failover.sh` (promote реплики), сервисы переподключаются через `target_session_attrs` | ≤ 15 мин | запись недоступна до переключения |
| Redis master | отказ узла | Sentinel | автоматическое переключение Sentinel | 15 с | чтение идёт напрямую в БД, медленнее |
| RabbitMQ (узел) | отказ узла | healthcheck кластера | quorum-очереди на 3 узлах, переподключение клиентов; outbox копит события | 10 с | нет |
| gateway | падение процесса | healthcheck | рестарт | 10 с | API недоступен; единая точка отказа, в проде решается двумя Nginx и keepalived |
| весь хост | отказ машины | внешний мониторинг | развёртывание на новом хосте + восстановление из бэкапа | ≤ 1 ч | полный простой |

**Как измеряем фактические значения.** Скрипт `scripts/chaos.sh` по очереди «роняет» компоненты (`docker kill`, `docker stop` узла БД) и записывает в `docs/incidents.csv` время отказа, обнаружения и восстановления (по метрике `up` в Prometheus и ответам API). Фактический MTTR — среднее по записям, фактический MTBF — длительность прогона, делённая на число отказов. Эти цифры и сравнение с целевыми идут в `docs/reliability.md`.

### RPO и RTO для данных

| Данные | Сценарий | RPO (сколько можно потерять) | RTO (за сколько восстанавливаем) | Механизм |
| --- | --- | --- | --- | --- |
| PostgreSQL | отказ primary | секунды (лаг асинхронной репликации) | 15 мин | streaming replica + failover |
| PostgreSQL | ошибочное удаление, порча данных | 6 ч | 30 мин | `pg_dump` каждые 6 ч, хранение 7 дней |
| PostgreSQL (опционально) | то же | минуты | 30 мин | архивация WAL через WAL-G |
| RabbitMQ | отказ узла | 0 для подтверждённых сообщений | 10 с | quorum-очереди, publisher confirms |
| Redis | отказ узла | не критично (кэш); ключи идемпотентности — до 1 с | 15 с | AOF `everysec`, Sentinel |

Проверка бэкапов обязательна: раз в неделю (и обязательно перед защитой) `scripts/restore.sh` восстанавливает свежий дамп во временную базу и сверяет число строк в `tickets`.

### Общие настройки отказоустойчивости

- У всех контейнеров `healthcheck` и `restart: unless-stopped`; сервисы стартуют только после `service_healthy` зависимостей.
- Лимиты ресурсов (`deploy.resources.limits`: CPU и память), чтобы один сервис не «съел» хост.
- Корректное завершение: `stop_grace_period: 20s`; по SIGTERM consumer'ы дочитывают текущее сообщение и делают `ack`, Uvicorn завершает активные запросы.
- Повторное подключение к БД, Redis и RabbitMQ при старте с экспоненциальной задержкой (tenacity), а не падение.
- Таймауты на все внешние вызовы: БД 5 с, Redis 0,5 с, SMTP 10 с.
- Деградация вместо отказа: без Redis сервис работает через БД; без RabbitMQ события копятся в outbox.

### Балансировка и масштабирование

- ticket-service работает минимум в 2 репликах; Nginx балансирует `least_conn` (сравнение с round-robin — один из экспериментов).
- **Автоскейлер** `scripts/autoscaler.py` раз в 15 секунд запрашивает Prometheus и вызывает `docker compose up -d --scale <сервис>=N --no-recreate`:
    - ticket-service: средняя загрузка CPU реплик > 70% или > 50 запросов/с на реплику → +1 (максимум 5); < 20% в течение 2 минут → −1 (минимум 2).
    - notification-service: глубина `notifications.q` > 100 сообщений → +1 воркер (максимум 5); очередь пуста 2 минуты → −1 (минимум 1).
- Вариант на повышенную оценку: перенос в k3s/minikube и HorizontalPodAutoscaler. Можно сделать в конце, если останется время.

### Нагрузочное тестирование (k6, каталог `loadtests/`)

| Сценарий | Профиль | Что доказываем |
| --- | --- | --- |
| `read_heavy.js` | 80% чтение списков и тикетов агентами, рост до 200 запросов/с | эффект кэша: прогон с Redis и без него, hit ratio, p95 |
| `write_burst.js` | клиенты массово создают тикеты, до 50 запросов/с | outbox и очереди справляются, уведомления в пределах SLO |
| `spike.js` | резкий скачок нагрузки в 10 раз на 2 минуты | срабатывание автоскейлера |
| `soak.js` | постоянная средняя нагрузка 30 минут | нет утечек памяти, стабильная задержка |
| `flood.js` | один IP шлёт сотни запросов в секунду | `429` от Nginx, при этом другой клиент обслуживается нормально |

В каждом сценарии пороги k6 совпадают с SLO (`http_req_failed < 0.5%`, `p(95) < 200`), так прогон сам говорит, выполнен ли SLO.

**Тесты отказоустойчивости под нагрузкой:** во время `read_heavy` убить реплику ticket-service; остановить Redis master; остановить узел RabbitMQ; переключить PostgreSQL. Для каждого — график ошибок и задержки в Grafana и строка в `incidents.csv`.

### Защита от DDoS

Защита многоуровневая, и на защите важно объяснить, почему одного автоскейлинга мало:

1. Nginx: `limit_req` и `limit_conn` по IP, таймауты против медленных атак, ограничение размера тела.
2. Сервисы: лимиты на пользователя (создание тикетов, попытки входа) в Redis — против атак с многих IP с одной учётки.
3. Автоскейлинг поглощает законный всплеск нагрузки, но ограничен сверху (максимум 5 реплик): иначе атака превращается в бесконечный рост расходов на ресурсы.
4. Кэш снижает нагрузку на БД при массовом чтении.
5. В реальной системе перед всем этим стоит внешний фильтр (CDN или облачная защита), это стоит упомянуть словами.

## Структура репозитория

Один монорепозиторий: всё запускается из корня, а общий код (проверка JWT, логирование, формат событий) лежит в пакете `libs/common` и ставится в образ каждого сервиса.

```
helpdesk/
├── docker-compose.yml          # базовый стенд
├── docker-compose.ha.yml       # реплики БД, Sentinel, кластер RabbitMQ
├── .env.example                # все переменные без реальных значений
├── Makefile                    # up, down, logs, test, migrate, backup, restore, check-net, load
├── libs/common/helpdesk_common/
│   ├── auth.py                 # get_current_user, require_role
│   ├── events.py               # Pydantic-модель конверта события
│   ├── logging.py              # structlog в JSON, request_id
│   └── health.py               # роутер /health/live, /health/ready
├── services/
│   ├── auth/
│   │   ├── app/
│   │   │   ├── main.py         # создание FastAPI, lifespan (подключения)
│   │   │   ├── config.py       # pydantic-settings
│   │   │   ├── db.py, cache.py
│   │   │   ├── models.py       # SQLAlchemy
│   │   │   ├── schemas.py      # Pydantic
│   │   │   ├── security.py     # хеши, выпуск JWT
│   │   │   └── api/auth.py, api/users.py
│   │   ├── alembic/ , alembic.ini
│   │   ├── tests/
│   │   ├── Dockerfile , pyproject.toml
│   ├── ticket/
│   │   └── app/ ... + domain/status.py, messaging/publisher.py,
│   │       messaging/outbox.py, messaging/consumer.py
│   ├── notification/
│   │   └── app/ main.py, consumer.py, rules.py, mailer.py, templates/
│   └── sla/
│       └── app/ ... + scheduler.py, consumer.py, policies.py
├── infra/
│   ├── nginx/nginx.conf
│   ├── postgres/init/01-init.sql , pg_hba.conf , postgresql.conf , replica-entrypoint.sh
│   ├── redis/redis.conf , users.acl , sentinel.conf
│   ├── rabbitmq/rabbitmq.conf , definitions.json
│   ├── backup/backup.sh
│   ├── prometheus/prometheus.yml , alerts.yml
│   ├── grafana/provisioning/ , dashboards/
│   └── firewall/iptables.sh
├── scripts/
│   ├── gen_keys.sh  gen_certs.sh  check_network.sh
│   ├── failover.sh  restore.sh  chaos.sh
│   ├── autoscaler.py  seed_demo_data.py
├── loadtests/ read_heavy.js write_burst.js spike.js soak.js flood.js
└── docs/
    ├── architecture.md  api.md  network.md
    ├── reliability.md   runbook.md  incidents.csv
    └── demo.md                 # сценарии защиты с командами
```

**Соглашения:** каждый Dockerfile многоэтапный, на базе `python:3.12-slim`, процесс запускается не от root (`USER app`); зависимости фиксируются в `pyproject.toml`; ветка `main` защищена, всё через pull request с ревью второго участника; FastAPI сам генерирует OpenAPI (`/docs` открыт только на этапе разработки).

## План разработки

Восемь этапов, первые четыре (задания 2–4) нужно закончить до коллоквиума в середине ноября, остальные — до экзамена во второй половине декабря. Даты ориентировочные, их стоит сверить с точными датами коллоквиума. **A** и **B** — участники пары: A ведёт данные (auth, ticket, PostgreSQL, Redis), B ведёт события и инфраструктуру (RabbitMQ, notification, sla, сети, Nginx, нагрузка). Каждый pull request ревьюит второй участник: это и контроль качества, и подготовка к вопросам по чужой части.

### Этап 0. Подготовка (26–29 октября)

- [ ] **A+B** Создать репозиторий, структуру каталогов из раздела выше, `.gitignore`, `.env.example`, защиту ветки `main`.
- [ ] **A** Пакет `libs/common`: настройки логирования (structlog, JSON), роутер health-проверок.
- [ ] **B** `Makefile` с целями `up`, `down`, `logs`, `ps`, `test`.
- [ ] **A+B** Договориться о формате событий (конверт из раздела ticket-service) и именах routing key — записать в `docs/api.md`.

### Этап 1. Задание 2: стенд и hello-world (до 2 ноября)

- [ ] **A** ticket-service: FastAPI-приложение с `GET /hello` и `GET /health/live`, Dockerfile (slim, не root).
- [ ] **A** PostgreSQL в compose: volume для данных, `01-init.sql` с тремя базами и тремя пользователями, healthcheck `pg_isready`.
- [ ] **B** Redis в compose: `redis.conf` с AOF и `maxmemory`, пока без ACL; healthcheck.
- [ ] **B** RabbitMQ (`rabbitmq:3.13-management`) в compose, healthcheck, консоль на `127.0.0.1:15672`.
- [ ] **B** Nginx: проксирует `/hello` на ticket-service.
- [ ] **A+B** `depends_on: condition: service_healthy`, проверка: `docker compose up -d` с нуля → `curl localhost/hello` отвечает.

**Готово, когда:** чистый клон репозитория поднимается одной командой, все контейнеры в статусе `healthy`.

### Этап 2. Задание 3: БД, кэш, брокер (до 9 ноября)

Аутентификации ещё нет: автор тикета временно берётся из заголовка `X-User-Id` (заглушка, удаляется на этапе 4).

- [ ] **A** SQLAlchemy-модели `tickets` и `comments`, первая миграция Alembic, контейнер `ticket-migrate`.
- [ ] **A** `POST /api/v1/tickets`, `GET /api/v1/tickets/{id}`, `GET /api/v1/tickets`, `POST /api/v1/tickets/{id}/status` (пока без проверки ролей).
- [ ] **A** Модуль `cache.py`: cache-aside для `ticket:{id}`, версионирование списков, заголовок `X-Cache`, деградация при недоступном Redis.
- [ ] **B** `definitions.json`: exchange `helpdesk.events`, очереди `notifications.q` и `sla.q`, привязки; загрузка при старте RabbitMQ.
- [ ] **B** `messaging/publisher.py` в ticket-service: подключение aio-pika, publisher confirms, публикация конверта после `COMMIT`.
- [ ] **B** notification-service, первая версия: consumer `notifications.q`, логирует событие и отправляет простое письмо в Mailpit; Mailpit в compose.
- [ ] **A+B** Тесты pytest: MISS → HIT → изменение → MISS; событие появляется в очереди после создания тикета.

**Готово, когда:** создание тикета пишет в БД, сбрасывает кэш, публикует событие; повторное чтение даёт `X-Cache: HIT`; письмо видно в Mailpit.

### Этап 3. Задание 4: сети и firewall (до 14 ноября)

- [ ] **B** Описать семь сетей с подсетями в compose, распределить сервисы по таблице из раздела «Сети и firewall», `internal: true` для сетей с данными.
- [ ] **B** Убрать все лишние `ports:`; наружу только gateway, консоли — на `127.0.0.1`.
- [ ] **A** Redis ACL (`users.acl`), отдельные пароли для сервисов, переход сервисов на свои учётки.
- [ ] **A** `pg_hba.conf` и `REVOKE CONNECT` для баз.
- [ ] **B** Пользователи RabbitMQ с правами и topic permissions.
- [ ] **B** `infra/firewall/iptables.sh` для `mon-net` (можно подготовить заранее, мониторинг появится на этапе 5) и проверка на Linux-ВМ.
- [ ] **B** `scripts/check_network.sh` с таблицей ожидаемых разрешений и запретов.
- [ ] **A+B** `docs/network.md`: схема сетей, матрица доступа, вывод `check_network.sh`.

**Готово, когда:** `check_network.sh` печатает только `OK`; оба участника могут объяснить, почему каждый запрет нужен.

### Коллоквиум (середина ноября)

- [ ] **A+B** Прогнать сценарии 1–3 и 6 из раздела «Сценарии демонстрации».
- [ ] **A+B** Каждый рассказывает чужую часть друг другу: A объясняет брокер и сети, B — кэш и БД.

### Этап 4. Полная функциональность (до 23 ноября)

- [ ] **A** auth-service целиком: модели, миграции, регистрация, логин, refresh с rotation, logout, `/me`, управление пользователями, seed админа, защита от перебора.
- [ ] **A** `scripts/gen_keys.sh`, ключи RS256 как docker secrets; `get_current_user` и `require_role` в `libs/common`; заглушка `X-User-Id` удалена.
- [ ] **A** ticket-service: все эндпоинты из таблицы, матрица переходов статусов, назначение, внутренние комментарии, история, оптимистичная блокировка, `stats`, лимит на создание.
- [ ] **B** sla-service: модели и миграции, политики, consumer `sla.q`, планировщик с `SKIP LOCKED`, публикация `ticket.sla_breached`, API статистики, `SLA_TIME_SCALE`.
- [ ] **B** notification-service: правила рассылки, шаблоны Jinja2, идемпотентность через Redis, retry-очередь и DLQ.
- [ ] **A** Consumer `ticket.sla.q` в ticket-service: эскалация и повышение приоритета.
- [ ] **B** Маршруты Nginx для auth и sla, лимиты `limit_req` / `limit_conn`, `X-Request-ID`, заголовки безопасности.
- [ ] **A+B** `scripts/seed_demo_data.py`: создаёт клиента, двух агентов и десяток тикетов разного приоритета.
- [ ] **A+B** Сквозной тест: клиент создаёт critical-тикет → письмо → SLA нарушен → письмо админу → тикет помечен `escalated`.

### Этап 5. Задание 5: надёжность (до 3 декабря)

- [ ] **A+B** Написать `docs/reliability.md`: SLI/SLO, целевые MTBF и MTTR по компонентам, RPO/RTO (таблицы из раздела «Надёжность» как основа).
- [ ] **A** PostgreSQL primary + replica, `replica-entrypoint.sh`, многохостовые строки подключения с `target_session_attrs`, `scripts/failover.sh`.
- [ ] **A** Контейнер `pg-backup`, `backup.sh` (каждые 6 ч, хранение 7 дней), `scripts/restore.sh`; проверить восстановление на практике.
- [ ] **A** Transactional outbox в ticket-service и фоновая публикация.
- [ ] **B** Redis master + replica + 3 Sentinel, переход клиентов на Sentinel.
- [ ] **B** Кластер RabbitMQ из 3 узлов, quorum-очереди, `x-delivery-limit`, подключение клиентов к списку узлов.
- [ ] **B** Prometheus, Grafana, экспортёры; дашборды RED, кэш, очереди, SLO; алерты `up == 0`, рост очереди, ошибки 5xx.
- [ ] **A+B** Лимиты ресурсов, `stop_grace_period`, повторные подключения (tenacity), таймауты на все внешние вызовы.
- [ ] **B** `scripts/chaos.sh` и `docs/incidents.csv`; посчитать фактические MTTR и сравнить с целевыми.

**Готово, когда:** отказ любого одного компонента не приводит к потере данных и событий, фактический MTTR укладывается в целевой.

### Этап 6. Задание 6: балансировка, нагрузка, DDoS (до 10 декабря)

- [ ] **B** Upstream с `resolve` и `least_conn`, `max_fails`, `proxy_next_upstream`; ticket-service в 2 репликах.
- [ ] **B** k6-сценарии `read_heavy`, `write_burst`, `spike`, `soak`, `flood` с порогами по SLO.
- [ ] **A** Прогоны с кэшем и без, сравнение p95 и нагрузки на БД; сравнение round-robin и `least_conn`.
- [ ] **B** `scripts/autoscaler.py` для ticket-service (CPU, RPS) и notification-service (глубина очереди).
- [ ] **A+B** Тесты отказоустойчивости под нагрузкой: убить реплику, Redis master, узел RabbitMQ, переключить PostgreSQL.
- [ ] **A+B** Демонстрация `flood.js`: атакующий IP получает `429`, обычный клиент работает.
- [ ] **A+B** (по желанию) Манифесты для k3s и HPA.
- [ ] **A+B** Результаты и графики в `docs/reliability.md`.

### Этап 7. Документация и подготовка к экзамену (до 16 декабря)

- [ ] **A+B** `docs/architecture.md` со схемой, `docs/api.md`, `docs/runbook.md` (что делать при отказе каждого компонента).
- [ ] **A+B** `docs/demo.md`: все сценарии защиты с точными командами.
- [ ] **A+B** Проверка «с нуля» на чистой машине: клон → `make up` → все сценарии проходят.
- [ ] **A+B** Две репетиции защиты: каждый показывает и объясняет всю систему, второй задаёт вопросы как преподаватель.

## Сценарии демонстрации на защите

Двенадцать сценариев покрывают все задания; сценарии 1–3 и 6 готовы к коллоквиуму, остальные — к экзамену. Точные команды для каждого хранятся в `docs/demo.md`, а токены для удобства получаются скриптом `scripts/login.sh <email>`.

1. **Создание тикета и уведомление.** Клиент делает `POST /api/v1/tickets` → в логах ticket-service видна запись и публикация события → в консоли RabbitMQ счётчик сообщений → письмо в Mailpit (`localhost:8025`).
2. **Кэш.** Два `GET /api/v1/tickets/{id}` подряд: `X-Cache: MISS`, затем `HIT`; `redis-cli GET ticket:{id}` показывает JSON. Смена статуса → снова `MISS`.
3. **Очередь как буфер.** `docker compose stop notification-service`, создать 5 тикетов → в консоли RabbitMQ в `notifications.q` 5 сообщений → `start` → все 5 писем приходят, очередь пустая.
4. **SLA и эскалация.** С `SLA_TIME_SCALE=60` создать critical-тикет и не трогать 15 секунд → в логах sla-service нарушение → письмо админу → у тикета `escalated: true` и повышенный приоритет.
5. **Роли и доступ.** Клиент запрашивает чужой тикет → `404`; клиент не видит внутренние комментарии; агент пытается вызвать `GET /api/v1/users` → `403`; запрещённый переход статуса → `422`.
6. **Сегментация.** Запуск `scripts/check_network.sh`; вручную: `docker compose exec gateway nc -zv -w2 postgres-primary 5432` → ошибка, `docker compose exec ticket-service nc -zv -w2 postgres-primary 5432` → успех; `redis-cli --user notif-svc GET ticket:1` → `NOPERM`.
7. **Защита от перебора.** Шесть неверных паролей подряд → `429` с `Retry-After`; частые запросы на `/auth/login` с одного IP режет Nginx.
8. **Отказ реплики под нагрузкой.** Запущен `k6 run loadtests/read_heavy.js`, выполняется `docker kill` одной реплики ticket-service → в Grafana нет всплеска ошибок, Nginx перестал слать трафик на убитую реплику, она перезапустилась.
9. **Отказ PostgreSQL.** Остановить primary → запись падает, чтение из кэша работает → `scripts/failover.sh` → сервисы переподключаются к новому primary; время восстановления записывается в `incidents.csv`.
10. **Бэкап и восстановление.** Админ удаляет тикет (`DELETE`) → `scripts/restore.sh tickets_db <последний дамп>` → тикет снова на месте.
11. **Защита от флуда.** `k6 run loadtests/flood.js` с одного адреса → массовые `429` в логах Nginx, при этом параллельный обычный клиент получает ответы с нормальной задержкой.
12. **Автоскейлинг.** `k6 run loadtests/spike.js` → `scripts/autoscaler.py` пишет в лог решение о масштабировании → `docker compose ps` показывает 3–5 реплик → по логу Nginx (`upstream_addr`) видно, что трафик идёт на новые реплики → после нагрузки реплики убираются.
