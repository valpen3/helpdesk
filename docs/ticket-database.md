# Таблицы и миграции ticket-service

Это первая часть этапа 2: подключение к PostgreSQL, модели `tickets` и `comments`,
первая миграция и автоматический порядок запуска. HTTP API тикетов — следующий шаг.

## Как это устроено

- `app/models.py` описывает таблицы на Python. Один объект Ticket — одна строка
  таблицы tickets. Поле comments.ticket_id связывает комментарий с тикетом.
- `app/db.py` создаёт engine (пул соединений) и фабрику сессий. Сессия — отдельная
  работа с базой в рамках запроса. `get_session` выдаёт её обработчику, затем
  закрывает; без явного commit изменения откатываются.
- `alembic/versions/0001_create_tickets_and_comments.py` создаёт таблицы, индексы
  и перечисления статусов/приоритетов. Таблица alembic_version хранит номер
  применённой миграции. Повторный upgrade head не создаёт таблицы заново.
- `ticket-migrate` выполняет миграцию и завершается. Приложение запускается
  только после успешного завершения миграции.

```text
PostgreSQL healthy → ticket-migrate Exited (0) → ticket-service healthy
```

Создание самих баз и пользователей по-прежнему выполняет
`infra/postgres/init/01-init.sh`. Alembic создаёт таблицы внутри tickets_db
под пользователем tickets_user. Суперпользователь приложению не нужен.

## Запуск

В Ubuntu/WSL из корня репозитория подготовьте `.env` по `.env.example`, задайте
свои пароли, затем выполните:

```bash
docker compose up -d --build --wait
docker compose ps -a
docker compose logs ticket-migrate
docker compose exec -T ticket-service alembic current
docker compose exec -T ticket-service alembic check
```

`ticket-migrate` со статусом `Exited (0)` — успешная одноразовая задача,
а не упавший сервис. `alembic current` должен показать `0001 (head)`.
`alembic check` сравнивает модели с фактической схемой: различий быть не должно.

Просмотр таблиц и проверки готовности:

```bash
docker compose exec -T postgres psql -U postgres -d tickets_db -c '\dt'
docker compose exec -T ticket-service python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health/ready').read().decode())"
```

Ожидаются таблицы tickets, comments и alembic_version. В ready должен быть
`"checks":{"postgres":"ok"}`. Liveness проверяет процесс независимо от БД.

## Настройки

Сервис и миграции используют одинаковые переменные DB_HOST, DB_PORT, DB_NAME,
DB_USER, DB_PASSWORD. Compose передаёт пароль из TICKETS_DB_PASSWORD в `.env`.
В Python он хранится как SecretStr; адрес создаётся через URL.create, поэтому
спецсимволы пароля не нужно вручную экранировать.

Все даты в таблицах имеют часовой пояс. При вставке БД выставляет created_at
и updated_at; дальнейшие обновления через SQLAlchemy выставляют updated_at.
При ручном SQL UPDATE updated_at надо менять явно: отдельного триггера нет.

## Следующее изменение схемы

1. Изменить модели.
2. В локальном окружении с настроенными DB_* создать миграцию:

   ```bash
   .venv/bin/alembic -c services/ticket/alembic.ini revision --autogenerate -m "describe change"
   ```

3. Проверить сгенерированный файл: autogenerate — помощник, а не гарантия.
4. Применить миграцию через `make migrate`, пересобрать/перезапустить приложение.

Контейнер содержит копию миграций на момент сборки. Новые миграции создавайте
в локальных исходниках и добавляйте в Git; файл, созданный только внутри
временного контейнера, пропадёт вместе с ним.

## Проверки

Обычные `make test` не требуют PostgreSQL. Интеграционный тест
`services/ticket/tests/test_database.py` включается переменной RUN_DATABASE_TESTS=1
в окружении, где есть PostgreSQL, применены миграции и установлены dev-зависимости.
Он проверяет запись тикета и комментария, значения по умолчанию, внешний ключ,
ограничение длины темы, каскадное удаление и откат незавершённой транзакции.

Откат `alembic downgrade base` удаляет эти таблицы и их данные. Проверять его
нужно только на отдельной тестовой базе. Обычный запуск требует `upgrade head`.

## Результат проверки реализации, 30 октября 2026

Проверка выполнялась на отдельном Compose-проекте `helpdesk-dbcheck` с новым
томом PostgreSQL. Текущий стенд `helpdesk` не изменялся.

| Проверка | Результат |
| --- | --- |
| Сборка образов приложения и мигратора | Успешно |
| Первый запуск с пустой БД | PostgreSQL healthy → мигратор Exited (0) → приложение healthy |
| `alembic current` | `0001 (head)` |
| `alembic check` | `No new upgrade operations detected.` |
| `/health/ready` | `{"status":"ok","checks":{"postgres":"ok"}}` |
| Общие тесты, hello-world и интеграционный тест БД | 9 passed, 1 warning |
| Ruff | All checks passed! |
| `alembic downgrade base` на тестовой БД | Таблицы tickets/comments и оба ENUM удалены |
| Повторный `alembic upgrade head` | Таблицы восстановлены |
| Ещё один `alembic upgrade head` | Успешно, схема не создаётся повторно |
| `alembic upgrade head --sql` | SQL сгенерирован без подключения для выполнения DDL |
| `git diff --check` | Ошибок нет |

Предупреждение тестов относится к устаревающему использованию httpx в
Starlette TestClient; все тесты проходят. GitHub workflow дополнен запуском
миграций на пустой базе, сравнением схемы и интеграционным тестом. Его новый
запуск на GitHub можно будет подтвердить после отправки изменений.

Основа реализации: [SQLAlchemy asyncio](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)
и [Alembic с asyncio](https://alembic.sqlalchemy.org/en/latest/cookbook.html#using-asyncio-with-alembic).
