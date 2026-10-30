#!/bin/sh
set -eu

: "${RABBITMQ_DEFAULT_PASS:?Set RABBITMQ_ADMIN_PASSWORD in .env}"
: "${NOTIF_MQ_PASSWORD:?Set NOTIF_MQ_PASSWORD in .env}"
umask 077
runtime_dir=/tmp/helpdesk-bootstrap
mkdir -p "$runtime_dir"

# hash_password works offline and prints a Base64 salted SHA-256 hash.
password_hash="$(rabbitmqctl -q hash_password "$RABBITMQ_DEFAULT_PASS")"
notif_password_hash="$(rabbitmqctl -q hash_password "$NOTIF_MQ_PASSWORD")"
for hash in "$password_hash" "$notif_password_hash"; do
    case "$hash" in
        ''|*[!A-Za-z0-9+/=]*)
            echo "RabbitMQ password hash has an unexpected format" >&2
            exit 1
            ;;
    esac
done

# Base64 has no '#' or '&', so it is safe in this sed replacement.
sed -e "s#__ADMIN_PASSWORD_HASH__#$password_hash#g" \
    -e "s#__NOTIF_PASSWORD_HASH__#$notif_password_hash#g" \
    /etc/rabbitmq/definitions.json > "$runtime_dir/definitions.json"
if [ "$(id -u)" = 0 ]; then
    chown rabbitmq:rabbitmq "$runtime_dir" "$runtime_dir/definitions.json"
fi

# Keep the official image's user switching and signal handling.
exec docker-entrypoint.sh "$@"
