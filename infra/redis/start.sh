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
    -e "s|__REDIS_REPLICATION_PASSWORD_HASH__|$(hash_password "${REDIS_REPLICATION_PASSWORD:?}")|g" \
    -e "s|__REDIS_SENTINEL_PASSWORD_HASH__|$(hash_password "${REDIS_SENTINEL_PASSWORD:?}")|g" \
    /usr/local/etc/redis/users.acl.template > "$acl_file"

# Persist Sentinel's REPLICAOF changes across restarts. Never reapply the
# bootstrap role after a node has been promoted or rejoined as a replica.
config=/data/redis.conf
quote() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'; }
if [ ! -f "$config" ]; then
    cp /usr/local/etc/redis/redis.conf "$config"
    if [ -n "${REDIS_REPLICA_OF:-}" ]; then
        printf '\nreplicaof %s 6379\n' "$REDIS_REPLICA_OF" >> "$config"
    fi
fi
# Secrets are regenerated rather than left stale in the persisted config.
sed -i '/^masteruser /d; /^masterauth /d; /^aclfile /d; /^replica-announce-ip /d' "$config"
printf '\nmasteruser replication\nmasterauth "%s"\naclfile %s\n' \
    "$(quote "$REDIS_REPLICATION_PASSWORD")" "$acl_file" >> "$config"
printf 'replica-announce-ip %s\n' "${REDIS_NODE_HOSTNAME:?}" >> "$config"
exec redis-server "$config"
