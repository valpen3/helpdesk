#!/bin/sh
set -eu

compose_file=${COMPOSE_FILE:-docker-compose.yml}
compose() { docker compose -f "$compose_file" "$@"; }

if [ "${1:-}" != "--confirm-primary-is-unavailable" ]; then
    echo "Usage: $0 --confirm-primary-is-unavailable" >&2
    echo "This stops the current primary before promoting the replica." >&2
    exit 2
fi

replica_id=$(compose ps -q postgres-replica)
if [ -z "$replica_id" ]; then
    echo "PostgreSQL replica container is not running" >&2
    exit 1
fi

echo "Fencing the old primary..."
compose stop postgres
echo "Promoting the replica..."
compose exec -T -u postgres postgres-replica \
    pg_ctl -D /var/lib/postgresql/data promote -w

echo "Waiting for postgres-router to route writes to the promoted node..."
attempt=0
until compose exec -T pg-backup psql --host=postgres-db --username=postgres \
    --dbname=postgres --tuples-only --no-align \
    --command "SELECT NOT pg_is_in_recovery()" 2>/dev/null | grep -qx t; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 30 ]; then
        echo "Router did not reach the promoted primary. Check PostgreSQL and HAProxy logs." >&2
        exit 1
    fi
    sleep 2
done

echo "Failover complete. The old primary remains stopped; do not start it before rebuilding it as a replica."
