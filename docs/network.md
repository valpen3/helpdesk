# Сети и правила доступа

В Compose заданы семь сетей с непересекающимися подсетями. Приложения подключаются только к тем сегментам, которые нужны для их работы. Сети баз данных, кэша, брокера, почты и мониторинга имеют `internal: true`.

## Сети

| Сеть | Подсеть | Внутренняя | Подключённые сервисы сейчас |
| --- | --- | --- | --- |
| `edge` | `10.10.1.0/24` | нет | `gateway`, `auth-service`, `ticket-service`, `sla-service` |
| `db-net` | `10.10.2.0/24` | да | PostgreSQL primary, replica, `postgres-router` (`postgres-db` alias), `pg-backup`, auth/ticket/SLA services and migrations |
| `cache-net` | `10.10.3.0/24` | да | `redis`, `auth-service`, `ticket-service`, `notification-service` |
| `broker-net` | `10.10.4.0/24` | да | `rabbitmq`, `ticket-service`, `notification-service`, `sla-service` |
| `mail-net` | `10.10.5.0/24` | да | `mailpit`, `notification-service` |
| `mon-net` | `10.10.6.0/24` | да | все постоянно работающие сервисы; Prometheus будет добавлен на этапе мониторинга |
| `admin-net` | `10.10.9.0/24` | нет | `rabbitmq`, `mailpit` |

Миграция подключена только к `db-net`: это одноразовая команда, которой не требуется мониторинговый доступ.

### Адреса в `mon-net`

| Сервис | Адрес |
| --- | --- |
| будущий Prometheus | `10.10.6.10` |
| `gateway` | `10.10.6.2` |
| `ticket-service` | `10.10.6.20` |
| PostgreSQL primary | `10.10.6.30` |
| PostgreSQL replica | `10.10.6.31` |
| `postgres-router` | `10.10.6.32` |
| `redis` | `10.10.6.40` |
| `rabbitmq` | `10.10.6.50` |
| `notification-service` | `10.10.6.60` |
| `mailpit` | `10.10.6.70` |
| `sla-service` | `10.10.6.80` |
| `auth-service` | `10.10.6.90` |

Эти адреса нужны для проверок и правил firewall. `postgres-db` — alias внутреннего `postgres-router`, который направляет приложения к текущему primary; сам primary и replica не получают этот alias. Приложения и миграции используют только адрес router внутри `db-net`.

## Матрица доступа

| Источник | Назначение | Порт | Причина |
| --- | --- | ---: | --- |
| host / пользователь | `gateway` | 80 | входной HTTP API |
| `gateway` | `ticket-service` | 8000 | проксирование API |
| `auth-service` | `postgres-db` | 5432 | Auth database access through `db-net` |
| `auth-service` | `redis` | 6379 | Login throttling and profile cache |
| `gateway` | `auth-service` | 8000 | проксирование API авторизации и пользователей |
| `ticket-service` | `postgres-db` | 5432 | база обращений через `db-net` |
| `ticket-service` | `redis` | 6379 | кэш обращений |
| `ticket-service` | `rabbitmq` | 5672 | публикация событий |
| `notification-service` | `rabbitmq` | 5672 | чтение событий |
| `notification-service` | `mailpit` | 1025 | отправка писем в тестовый SMTP |
| `notification-service` | `redis` | 6379 | дедупликация успешных email-доставок |
| `sla-service` | `postgres-db` | 5432 | хранение SLA-сроков и обработанных событий |
| `sla-service` | `rabbitmq` | 5672 | чтение событий заявок и публикация нарушений SLA |
| `gateway` | `sla-service` | 8000 | проксирование `/api/v1/sla`; SLA проверяет access token локально по публичному ключу auth-service |
| Prometheus (этап 5) | сервисы в `mon-net` | метрики | сбор метрик; доступ будет открыт отдельной учётной записи/IP |

Запрещённые проверки: `notification-service` не должен соединяться с `ticket-service` (`10.10.6.20:8000`) или `postgres` (`10.10.6.30:5432`); `ticket-service` не должен соединяться с `mailpit` (`10.10.6.70:1025`). Сервисы не должны использовать общую сеть мониторинга как обход своих рабочих сегментов.

## Firewall

`infra/firewall/iptables.sh` создаёт отдельную цепочку в `DOCKER-USER`. Она разрешает ответы на уже открытые соединения, весь трафик от будущего Prometheus (`10.10.6.10`) и точечные пары из матрицы выше. Остальные прямые соединения внутри `mon-net` логируются и блокируются. Точечные разрешения нужны потому, что Docker DNS может вернуть приложению адрес целевого контейнера именно из `mon-net`; без них штатный вызов мог бы попасть под блокировку.

Скрипт рассчитан на Linux-хост с Docker Engine и запускается от root:

```bash
sudo bash infra/firewall/iptables.sh
```

Проверки запускаются после `docker compose up -d --wait`:

```bash
bash scripts/check_network.sh
```

Ожидаемый результат — строки `OK ALLOW` для необходимых рабочих соединений и `OK DENY` для запрещённых. В конце скрипт печатает итог `Все сетевые проверки завершились с OK.` На Docker Desktop под Windows firewall следует проверять на Linux-ВМ: Docker Desktop выполняет сетевую маршрутизацию внутри своей виртуальной машины.
