#!/bin/bash
# Выполняется образом postgres ОДИН раз — при первом запуске с пустым томом данных.
# Создаёт три базы и трёх пользователей; пароли приходят из переменных окружения.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v auth_pw="$AUTH_DB_PASSWORD" \
  -v tickets_pw="$TICKETS_DB_PASSWORD" \
  -v sla_pw="$SLA_DB_PASSWORD" <<'EOSQL'
CREATE ROLE auth_user    LOGIN PASSWORD :'auth_pw';
CREATE ROLE tickets_user LOGIN PASSWORD :'tickets_pw';
CREATE ROLE sla_user     LOGIN PASSWORD :'sla_pw';

CREATE DATABASE auth_db    OWNER auth_user;
CREATE DATABASE tickets_db OWNER tickets_user;
CREATE DATABASE sla_db     OWNER sla_user;

-- По умолчанию к любой базе может подключиться любой пользователь (PUBLIC).
-- Закрываем: к базе ходит только её владелец.
REVOKE ALL ON DATABASE auth_db    FROM PUBLIC;
REVOKE ALL ON DATABASE tickets_db FROM PUBLIC;
REVOKE ALL ON DATABASE sla_db     FROM PUBLIC;
EOSQL

echo "helpdesk: базы auth_db, tickets_db, sla_db созданы"
