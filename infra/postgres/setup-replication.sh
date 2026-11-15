#!/bin/sh
set -eu

: "${PGHOST:=postgres}"
: "${PGPORT:=5432}"
: "${POSTGRES_USER:=postgres}"
: "${POSTGRES_PASSWORD:?Set POSTGRES_PASSWORD}"
: "${REPLICATION_PASSWORD:?Set REPLICATION_PASSWORD}"

export PGPASSWORD="$POSTGRES_PASSWORD"
psql --host="$PGHOST" --port="$PGPORT" --username="$POSTGRES_USER" \
    --dbname=postgres --set=ON_ERROR_STOP=1 --set=replication_pw="$REPLICATION_PASSWORD" <<'SQL'
SELECT format(
    'CREATE ROLE replicator WITH REPLICATION LOGIN PASSWORD %L', :'replication_pw'
)
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'replicator')
\gexec
ALTER ROLE replicator WITH REPLICATION LOGIN PASSWORD :'replication_pw';
SELECT pg_create_physical_replication_slot('helpdesk_replica')
WHERE NOT EXISTS (
    SELECT 1 FROM pg_replication_slots WHERE slot_name = 'helpdesk_replica'
);
SQL
