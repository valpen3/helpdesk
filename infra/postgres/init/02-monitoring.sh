#!/bin/sh
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
    -v metrics_pw="${EXPORTER_DB_PASSWORD:?}" <<'SQL'
CREATE ROLE metrics LOGIN PASSWORD :'metrics_pw';
GRANT pg_monitor TO metrics;
SQL
