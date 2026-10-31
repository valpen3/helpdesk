#!/usr/bin/env bash
set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker не найден в PATH." >&2
  exit 2
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "ERROR: Docker Compose недоступен." >&2
  exit 2
fi

# Источник | назначение | порт | ожидаемый результат.
# ALLOW проверяет обычный рабочий канал между сервисами.
# DENY обращается к фиксированному IP в mon-net: DNS не может создать ложный OK.
checks=(
  "ALLOW|ticket-service|postgres-db|5432"
  "ALLOW|ticket-service|redis|6379"
  "ALLOW|ticket-service|rabbitmq|5672"
  "ALLOW|notification-service|rabbitmq|5672"
  "ALLOW|notification-service|mailpit|1025"
  "DENY|notification-service|10.10.6.20|8000"
  "DENY|notification-service|10.10.6.30|5432"
  "DENY|ticket-service|10.10.6.70|1025"
)

failed=0

run_check() {
  local expected="$1" source="$2" destination="$3" port="$4" actual

  if ! docker compose exec -T "$source" true >/dev/null 2>&1; then
    printf 'FAIL source service is not running: %s\n' "$source"
    failed=1
    return
  fi

  if ! docker compose exec -T "$source" sh -c 'command -v nc >/dev/null 2>&1' >/dev/null 2>&1; then
    printf 'FAIL netcat is missing in source service: %s\n' "$source"
    failed=1
    return
  fi

  if docker compose exec -T "$source" nc -z -w 2 "$destination" "$port" >/dev/null 2>&1; then
    actual="ALLOW"
  else
    actual="DENY"
  fi

  if [[ "$actual" == "$expected" ]]; then
    printf 'OK %s %s -> %s:%s\n' "$expected" "$source" "$destination" "$port"
  else
    printf 'FAIL expected %s, got %s: %s -> %s:%s\n' \
      "$expected" "$actual" "$source" "$destination" "$port"
    failed=1
  fi
}

# nginx образ уже содержит wget; эта проверка подтверждает путь через edge.
if docker compose exec -T gateway wget -q -T 3 -O /dev/null \
  http://ticket-service:8000/health/live >/dev/null 2>&1; then
  echo "OK ALLOW gateway -> ticket-service:8000"
else
  echo "FAIL expected ALLOW: gateway -> ticket-service:8000"
  failed=1
fi

for check in "${checks[@]}"; do
  IFS='|' read -r expected source destination port <<< "$check"
  run_check "$expected" "$source" "$destination" "$port"
done

if [[ "$failed" -ne 0 ]]; then
  exit 1
fi

echo "Все сетевые проверки завершились с OK."
