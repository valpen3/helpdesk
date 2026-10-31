#!/bin/sh
set -eu

: "${AUTH_REDIS_PASSWORD:?AUTH_REDIS_PASSWORD is required}"
: "${TICKET_REDIS_PASSWORD:?TICKET_REDIS_PASSWORD is required}"
: "${NOTIF_REDIS_PASSWORD:?NOTIF_REDIS_PASSWORD is required}"
: "${EXPORTER_REDIS_PASSWORD:?EXPORTER_REDIS_PASSWORD is required}"

umask 077
acl_file="$(mktemp /tmp/users.acl.XXXXXX)"
hash_password() {
    printf '%s' "$1" | sha256sum | cut -d ' ' -f 1
}

sed \
    -e "s|__AUTH_REDIS_PASSWORD_HASH__|$(hash_password "$AUTH_REDIS_PASSWORD")|g" \
    -e "s|__TICKET_REDIS_PASSWORD_HASH__|$(hash_password "$TICKET_REDIS_PASSWORD")|g" \
    -e "s|__NOTIF_REDIS_PASSWORD_HASH__|$(hash_password "$NOTIF_REDIS_PASSWORD")|g" \
    -e "s|__EXPORTER_REDIS_PASSWORD_HASH__|$(hash_password "$EXPORTER_REDIS_PASSWORD")|g" \
    /usr/local/etc/redis/users.acl.template > "$acl_file"

exec redis-server /usr/local/etc/redis/redis.conf --aclfile "$acl_file"
