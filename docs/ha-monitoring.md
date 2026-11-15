# Участник B: отказоустойчивость кэша, брокера и мониторинг

## Состав стенда

Redis: `redis`, `redis-replica-1`, `redis-replica-2` и три `sentinel-1..3`.
Имя группы — `helpdesk`, кворум Sentinel — 2. После 5 секунд недоступности
мастера начинается выбор нового. Redis сохраняет AOF и переписанную конфигурацию
роли в отдельном томе каждого узла; Sentinel сохраняет topology/epochs в своих томах.
Перезапуск бывшего мастера не превращает его обратно в независимый мастер.
Для Redis и Sentinel заданы фиксированные IP в cache-net (10.10.3.10–12 и
10.10.3.21–23). Docker удаляет DNS-имя остановленного контейнера: синхронные DNS
таймауты могут вводить Sentinel в TILT. Прямые IP исключают это из пути failover.

Auth, ticket и notification используют `redis.asyncio.sentinel.Sentinel.master_for`.
Пароль Sentinel отделён от паролей ACL приложений; репликация имеет отдельного
пользователя `replication`. Redis не публикует порт на хост. Ticket сохраняет
cache-aside fallback, auth при недоступности лимитера возвращает 503,
notification отправляет неудачные операции идемпотентности на повтор.
Асинхронная репликация Redis допускает потерю последних записей при отказе;
Sentinel не превращает Redis в строго согласованное хранилище.

RabbitMQ: `rabbitmq`, `rabbitmq-2`, `rabbitmq-3`, общий Erlang cookie,
peer discovery `classic_config`, `pause_minority`. Все рабочие очереди, retry,
DLQ и диагностическая `ha.probe.q` — quorum. `rabbitmq-bootstrap` ждёт три узла
и добавляет их в состав каждой очереди: импорт definitions на первом узле
может произойти раньше присоединения остальных. Приложения ждут окончания bootstrap.
Retry-очередь использует at-least-once dead lettering и `reject-publish`.

Общий `ClusterConnection` меняет узел при разрыве и при неудаче очередного
подключения. aio-pika восстанавливает каналы и подписки, heartbeat — 10 секунд,
интервал переподключения — 1 секунда. Публикации остаются persistent с confirms
и `mandatory`; неподтверждённые бизнес-публикации автоматически не повторяются.
Брокер не заменяет transactional outbox: сбой между COMMIT и publish всё ещё
может оставить событие неопубликованным.

Три контейнера на одном Docker-хосте демонстрируют отказ узла, а не устойчивость
к отказу самого хоста. Для этого нужны разные машины/зоны.

## Запуск на чистом стенде

Добавьте новые переменные из `.env.example` в локальный `.env`: пароли
репликации, Sentinel, пользователя PostgreSQL `metrics`, Grafana и Erlang cookie.
Задайте собственные значения; `.env` и JWT-ключи не коммитятся.
Требуются Docker Compose с поддержкой `!override` и минимум около 4 ГБ свободной RAM.

```bash
bash scripts/gen_keys.sh                    # только если ключей ещё нет
docker compose --profile monitoring config -q
docker compose --profile monitoring up -d --build --wait
python3 scripts/check_monitoring.py
docker compose exec rabbitmq rabbitmqctl cluster_status
docker compose exec rabbitmq rabbitmqctl list_queues name type members online
docker compose exec sentinel-1 sh -c \
  'REDISCLI_AUTH="$REDIS_SENTINEL_PASSWORD" redis-cli -p 26379 SENTINEL ckquorum helpdesk'
```

При обновлении существующего стенда нельзя менять classic queue на quorum
на месте: тип очереди неизменяемый. Сначала остановите старый стенд без удаления
томов (`docker compose down`), сохраните его данные и используйте новый project
с отдельными томами. Для переноса накопленных сообщений нужна отдельная процедура
drain/shovel с проверкой доставки; `export definitions` сообщений не сохраняет.
Не используйте `down -v` для миграции. Старый и новый стенд с указанными фиксированными
подсетями одновременно на одном Docker-хосте не запускаются.

Роль PostgreSQL `metrics` создаётся init-скриптом на новой БД и получает только
`pg_monitor`. На уже инициализированной БД администратор должен отдельно создать
эту роль, передав пароль через psql-переменную, и перезагрузить `pg_hba.conf`.
Пароль экспортёра должен совпадать с `EXPORTER_DB_PASSWORD`.

## Мониторинг

Профиль `monitoring` включает Prometheus, Alertmanager, Grafana, Redis exporter,
отдельный Sentinel exporter, PostgreSQL exporter, Nginx exporter и blackbox exporter.
RabbitMQ отдаёт метрики встроенным `rabbitmq_prometheus` на каждом из трёх узлов.
Все четыре FastAPI-сервиса отдают `/metrics`: число запросов и latency histogram
с шаблоном маршрута вместо ID. SLA сохраняет существующие предметные метрики.
Blackbox проверяет readiness всех сервисов и `/health` gateway.

| Интерфейс | Адрес | Доступ |
| --- | --- | --- |
| Grafana | http://127.0.0.1:3000 | admin / `GRAFANA_ADMIN_PASSWORD` |
| Prometheus | http://127.0.0.1:9090 | только loopback хоста |
| Alertmanager | http://127.0.0.1:9093 | только loopback хоста |
| RabbitMQ management | http://127.0.0.1:15672 | admin / `RABBITMQ_ADMIN_PASSWORD` |

Grafana автоматически получает datasource и два дашборда: обзор сервисов
(readiness, RPS, p95, 5xx, Nginx, PostgreSQL, активные alerts) и состояние кластеров
(доступность, репликация, память и hit ratio Redis, очереди и consumers RabbitMQ).
Метрики сервисов, Redis и AMQP не публикуются через gateway. `/nginx_status`
разрешён только из mon-net. Firewall учитывает новые узлы и экспортёры.

Правила оповещают о недоступных targets, readiness, Redis/репликах/Sentinel,
потере резервирования RabbitMQ, backlog, DLQ, PostgreSQL и доле 5xx > 5%.
События видны в Alertmanager и Grafana; внешний получатель не настроен.
Для доставки писем/webhook добавьте явный receiver в `infra/prometheus/alertmanager.yml`.
Для проверки срабатывания остановите один RabbitMQ-узел на >15 секунд и откройте
`/alerts` Prometheus: `RabbitMQQuorumAtRisk` должен перейти в firing.

## Сценарий отказов и журнал

Запускать только на отдельном учебном стенде. Скрипт отказывается работать,
если `COMPOSE_PROJECT_NAME` не начинается с `helpdesk-ha-`.

```bash
export COMPOSE_PROJECT_NAME=helpdesk-ha-lab
docker compose --profile monitoring up -d --build --wait
python3 scripts/check_monitoring.py
python3 scripts/failure_scenario.py --cycles 2 --output artifacts/ha-results
docker compose --profile monitoring ps
```

Один долгоживущий процесс создаёт Redis Sentinel client, robust AMQP connection
и канал один раз. Каждые 200 мс (плюс длительность операций) он:

1. Проверяет заранее реплицированный Redis marker, делает запись/чтение нового ключа.
2. Публикует persistent сообщение с confirm в quorum-очередь, читает и подтверждает
   его с проверкой уникального payload.
3. Записывает UTC Unix timestamp, результаты и выбранные узлы.

Контроллер останавливает **текущего** Redis-мастера, затем **текущий** узел AMQP;
каждый раз ждёт устойчивого восстановления на другом узле и возвращает остановленный
контейнер в `finally`. Пока узел остановлен, сценарий также ждёт реального
срабатывания соответствующего alert в Prometheus и проверяет readiness четырёх
приложений. Перезапуска клиентов нет. Перед первым отказом Redis `WAIT 2`
подтверждает marker на обеих репликах. Между отказами есть здоровый интервал.
Два цикла дают четыре управляемых отказа. Пропажа marker, подтверждённого сообщения,
непереключившийся клиент или превышение 90 секунд завершают сценарий с ошибкой.

`samples.jsonl` — исходные наблюдения, `probe.log` — диагностика клиента,
`summary.json` — журнал инъекций/восстановлений, commit SHA, ссылка на CI и расчёты:

- outage начинается переходом успешной операции в ошибку, заканчивается первой успешной;
- MTTR = суммарное наблюдаемое время неуспешных операций / число outage;
- MTBF = суммарное наблюдаемое время успешных операций / число outage;
- если ошибок не наблюдалось, MTTR/MTBF равны `null`, а не выдуманному нулю;
- незаконченный outage помечается censored, MTTR не рассчитывается.

Это фактические измерения **управляемого эксперимента**, а не оценка MTBF в эксплуатации:
частоту сбоев здесь задаёт оператор. Погрешность границ ограничена расстоянием между
соседними samples, включая таймауты операций. Время `injected_at → recovered_at`
также сохраняется, но не подменяет измеренную длительность outage.

CI проверяет unit-тесты, реальные PostgreSQL/Redis, состав quorum-очередей,
promtool/amtool и правила alerts, доступность всех scrape targets и четыре отказа.
В каждом запуске журнал доступен как artifact `ha-failure-results` даже при ошибке.
Проверенный журнал добавляется в `docs/results/` с SHA измеряемого кода.

## Основания конфигурации

- [Redis Sentinel](https://redis.io/docs/latest/operate/oss_and_stack/management/sentinel/).
- [Redis-py Sentinel](https://redis.readthedocs.io/en/stable/connections.html#sentinelclient).
- [RabbitMQ cluster formation](https://www.rabbitmq.com/docs/3.13/cluster-formation).
- [RabbitMQ quorum queues](https://www.rabbitmq.com/docs/3.13/quorum-queues).
- [Prometheus alerting rules](https://prometheus.io/docs/prometheus/latest/configuration/alerting_rules/).
