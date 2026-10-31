#!/usr/bin/env bash
set -euo pipefail

docker compose exec -T redis sh -c '
  REDISCLI_AUTH="$TICKET_REDIS_PASSWORD" redis-cli --user ticket-svc SET ticket:acl-check ok >/dev/null
  result=$(REDISCLI_AUTH="$NOTIF_REDIS_PASSWORD" redis-cli --user notif-svc GET ticket:acl-check 2>&1 || true)
  case "$result" in
    NOPERM*) echo "OK Redis ACL isolates ticket keys" ;;
    *) echo "FAIL expected NOPERM, received: $result" >&2; exit 1 ;;
  esac
'

docker compose exec -T ticket-service python -c '
import asyncio
import os
import asyncpg

async def check():
    try:
        connection = await asyncpg.connect(
            host=os.environ["DB_HOST"],
            port=int(os.environ["DB_PORT"]),
            database="auth_db",
            user=os.environ["DB_USER"],
            password=os.environ["DB_PASSWORD"],
        )
    except (
        asyncpg.InvalidAuthorizationSpecificationError,
        asyncpg.InsufficientPrivilegeError,
    ):
        print("OK PostgreSQL rejects tickets_user on auth_db")
        return
    else:
        await connection.close()
        raise SystemExit("FAIL tickets_user can connect to auth_db")

asyncio.run(check())
'
