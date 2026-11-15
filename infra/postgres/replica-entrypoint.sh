#!/bin/sh
set -eu

: "${PGDATA:=/var/lib/postgresql/data}"
: "${PG_PRIMARY_HOST:=postgres}"
: "${PG_PRIMARY_PORT:=5432}"
: "${REPLICATION_USER:=replicator}"
: "${REPLICATION_PASSWORD:?Set REPLICATION_PASSWORD}"

if [ ! -s "$PGDATA/PG_VERSION" ]; then
    until pg_isready --host="$PG_PRIMARY_HOST" --port="$PG_PRIMARY_PORT" --username=postgres; do
        echo "Waiting for PostgreSQL primary at $PG_PRIMARY_HOST:$PG_PRIMARY_PORT"
        sleep 2
    done
    install -d -o postgres -g postgres "$PGDATA"
    PGPASSWORD="$REPLICATION_PASSWORD" su-exec postgres pg_basebackup \
        --host="$PG_PRIMARY_HOST" --port="$PG_PRIMARY_PORT" \
        --username="$REPLICATION_USER" --pgdata="$PGDATA" \
        --slot=helpdesk_replica --format=plain --wal-method=stream \
        --progress --write-recovery-conf
fi

escaped_password=$(printf '%s' "$REPLICATION_PASSWORD" | sed 's/\\/\\\\/g; s/:/\\:/g')
printf '%s:%s:*:%s:%s\n' "$PG_PRIMARY_HOST" "$PG_PRIMARY_PORT" \
    "$REPLICATION_USER" "$escaped_password" > "$PGDATA/.pgpass"
chown postgres:postgres "$PGDATA/.pgpass"
chmod 600 "$PGDATA/.pgpass"
export PGPASSFILE="$PGDATA/.pgpass"

exec /usr/local/bin/docker-entrypoint.sh "$@"
