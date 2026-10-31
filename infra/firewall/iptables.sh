#!/usr/bin/env bash
set -Eeuo pipefail

# Ограничивает прямые соединения между контейнерами в mon-net.
# Вызывать на Linux-хосте после запуска Docker Compose, от root.
readonly chain="HELPDESK-MON-NET"
readonly mon_cidr="10.10.6.0/24"
readonly prometheus_ip="10.10.6.10"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "ERROR: этот скрипт работает на Linux-хосте, не в Docker Desktop/Windows." >&2
  exit 1
fi

if [[ "$(id -u)" -ne 0 ]]; then
  echo "ERROR: запустите скрипт через sudo." >&2
  exit 1
fi

if ! command -v iptables >/dev/null 2>&1; then
  echo "ERROR: iptables не найден." >&2
  exit 1
fi

if ! iptables -w -nL DOCKER-USER >/dev/null 2>&1; then
  echo "ERROR: цепочка DOCKER-USER не найдена. Сначала запустите Docker Engine." >&2
  exit 1
fi

# Docker bridge-трафик должен проходить через netfilter, чтобы правила увидели
# соединения между контейнерами одной сети.
if [[ "$(sysctl -n net.bridge.bridge-nf-call-iptables 2>/dev/null || true)" != "1" ]]; then
  modprobe br_netfilter
  sysctl -w net.bridge.bridge-nf-call-iptables=1
fi

# Собственная цепочка позволяет повторно применять скрипт без дублирования правил.
iptables -w -N "$chain" 2>/dev/null || true
iptables -w -F "$chain"
iptables -w -A "$chain" -s "$mon_cidr" -d "$mon_cidr" \
  -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -w -A "$chain" -s "$prometheus_ip" -d "$mon_cidr" -j ACCEPT
# Сервисы могут получить от Docker DNS адрес коллеги в mon-net. Разрешаем
# только пары из рабочей матрицы, чтобы эти адреса не ломали штатные вызовы.
iptables -w -A "$chain" -s 10.10.6.2 -d 10.10.6.20 -p tcp --dport 8000 -j ACCEPT
iptables -w -A "$chain" -s 10.10.6.20 -d 10.10.6.30 -p tcp --dport 5432 -j ACCEPT
iptables -w -A "$chain" -s 10.10.6.20 -d 10.10.6.40 -p tcp --dport 6379 -j ACCEPT
iptables -w -A "$chain" -s 10.10.6.20 -d 10.10.6.50 -p tcp --dport 5672 -j ACCEPT
iptables -w -A "$chain" -s 10.10.6.60 -d 10.10.6.50 -p tcp --dport 5672 -j ACCEPT
iptables -w -A "$chain" -s 10.10.6.60 -d 10.10.6.70 -p tcp --dport 1025 -j ACCEPT
iptables -w -A "$chain" -s "$mon_cidr" -d "$mon_cidr" \
  -m limit --limit 5/min --limit-burst 10 \
  -j LOG --log-prefix "HELPDESK-MON-DROP " --log-level 4
iptables -w -A "$chain" -s "$mon_cidr" -d "$mon_cidr" -j DROP
iptables -w -A "$chain" -j RETURN

if ! iptables -w -C DOCKER-USER -j "$chain" >/dev/null 2>&1; then
  iptables -w -I DOCKER-USER 1 -j "$chain"
fi

echo "OK: Prometheus ($prometheus_ip) and listed service connections are allowed; other mon-net traffic is blocked."
