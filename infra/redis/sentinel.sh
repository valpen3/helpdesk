#!/bin/sh
set -eu
umask 077
config=/data/sentinel.conf
quote() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'; }
if [ ! -f "$config" ]; then
    cat > "$config" <<EOF
port 26379
bind 0.0.0.0
dir /data
sentinel resolve-hostnames yes
sentinel announce-hostnames yes
sentinel monitor helpdesk redis 6379 2
sentinel down-after-milliseconds helpdesk 5000
sentinel failover-timeout helpdesk 30000
sentinel parallel-syncs helpdesk 1
EOF
fi
sed -i '/^requirepass /d; /^sentinel auth-user /d; /^sentinel auth-pass /d; /^sentinel sentinel-pass /d; /^sentinel announce-ip /d' "$config"
password="$(quote "${REDIS_SENTINEL_PASSWORD:?}")"
cat >> "$config" <<EOF
requirepass "$password"
sentinel auth-user helpdesk sentinel
sentinel auth-pass helpdesk "$password"
sentinel sentinel-pass "$password"
sentinel announce-ip ${SENTINEL_HOSTNAME:?}
EOF
exec redis-server "$config" --sentinel
