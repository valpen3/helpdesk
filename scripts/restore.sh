#!/bin/sh
set -eu

if [ "$#" -ne 2 ]; then
    echo "Usage: $0 <backup.dump> <new-empty-database>" >&2
    exit 2
fi

dump_file=$1
target_db=$2
[ -r "$dump_file" ] || { echo "Backup is not readable: $dump_file" >&2; exit 1; }
case "$target_db" in
    *[!a-zA-Z0-9_]*|'') echo "Target database name may contain only letters, digits and underscores" >&2; exit 2 ;;
esac

case "$target_db" in
    auth_*) owner=auth_user ;;
    tickets_*) owner=tickets_user ;;
    sla_*) owner=sla_user ;;
    *) echo "Target name must start with auth_, tickets_ or sla_" >&2; exit 2 ;;
esac

export PGPASSWORD=${POSTGRES_PASSWORD:?Set POSTGRES_PASSWORD}
db_user=${POSTGRES_USER:-postgres}
host=${PGHOST:-localhost}
port=${PGPORT:-5432}
if psql --host="$host" --port="$port" --username="$db_user" --dbname=postgres \
    --tuples-only --no-align --command "SELECT 1 FROM pg_database WHERE datname = '$target_db'" | grep -q 1; then
    echo "Refusing to overwrite existing database: $target_db" >&2
    exit 1
fi

createdb --host="$host" --port="$port" --username="$db_user" --owner="$owner" "$target_db"
if ! pg_restore --host="$host" --port="$port" --username="$db_user" --dbname="$target_db" \
    --no-owner --role="$owner" "$dump_file"; then
    dropdb --host="$host" --port="$port" --username="$db_user" "$target_db" || true
    exit 1
fi

echo "Restore succeeded into $target_db. Verify application data before switching production traffic."
