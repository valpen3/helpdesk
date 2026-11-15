#!/bin/sh
set -eu

: "${PGHOST:=postgres-db}"
: "${PGPORT:=5432}"
: "${BACKUP_DIR:=/backups}"
: "${BACKUP_INTERVAL_SECONDS:=21600}"
: "${BACKUP_RETENTION_DAYS:=7}"

databases="auth_db:AUTH_DB_PASSWORD:auth_user tickets_db:TICKETS_DB_PASSWORD:tickets_user sla_db:SLA_DB_PASSWORD:sla_user"

backup_once() {
    timestamp=$(date -u +%Y%m%d_%H%M%S)
    for entry in $databases; do
        db=${entry%%:*}
        remainder=${entry#*:}
        password_var=${remainder%%:*}
        user=${remainder##*:}
        password=$(printenv "$password_var")
        if [ -z "$password" ]; then
            echo "$password_var is required" >&2
            return 1
        fi
        destination="$BACKUP_DIR/$db"
        mkdir -p "$destination"
        file="$destination/${db}_${timestamp}.dump"
        echo "Backing up $db to $file"
        PGPASSWORD="$password" pg_dump --host="$PGHOST" --port="$PGPORT" \
            --username="$user" --dbname="$db" --format=custom --no-owner --file="$file"
        pg_restore --list "$file" >/dev/null
        find "$destination" -type f -name '*.dump' -mtime "+$BACKUP_RETENTION_DAYS" -delete
    done
}

while :; do
    if backup_once; then
        echo "Backup cycle completed"
    else
        echo "Backup cycle failed; retrying in 60 seconds" >&2
        sleep 60
        continue
    fi
    sleep "$BACKUP_INTERVAL_SECONDS"
done
