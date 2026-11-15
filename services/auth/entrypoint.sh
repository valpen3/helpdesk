#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
    exec "$@"
fi

install -o 10002 -g 10002 -m 600 /run/secrets/jwt_private.pem /tmp/jwt_private.pem
install -o 10002 -g 10002 -m 644 /run/secrets/jwt_public.pem /tmp/jwt_public.pem
export JWT_PRIVATE_KEY_PATH=/tmp/jwt_private.pem
export JWT_PUBLIC_KEY_PATH=/tmp/jwt_public.pem
# setpriv changes IDs but leaves root's environment, including HOME, intact.
# asyncpg looks for optional PostgreSQL SSL keys under HOME when connecting.
export HOME=/home/app USER=app LOGNAME=app
exec setpriv --reuid=10002 --regid=10002 --init-groups "$@"
