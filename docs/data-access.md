# Redis and PostgreSQL access boundaries

Redis credentials are read from `.env`. At container startup, `infra/redis/start.sh`
hashes them and writes a mode-600 ACL file under `/tmp`; plaintext passwords are
not written to the repository or mounted files. The default Redis user is off.
Each service account can access only its key prefixes, and the exporter account
has read-only health and metrics commands.

The ticket service uses `ticket-svc` and the existing `TICKET_REDIS_PASSWORD`.
PostgreSQL keeps the existing one-role-per-database setup and revokes `CONNECT`
from `PUBLIC`. `infra/postgres/pg_hba.conf` allows service roles only from the
planned `db-net` subnet (`10.10.2.0/24`) and rejects all other TCP clients.

After the stack is running, check both boundaries with:

```bash
bash scripts/check_data_access.sh
```

The check confirms the ticket Redis user can write its own key, the notification
user cannot read ticket keys, and `tickets_user` cannot connect to `auth_db`.
