# Auth service

`auth-service` owns the `auth_db` users and refresh-token tables. It signs RS256
tokens; other services verify access tokens locally with the public key and do
not call auth-service for each request.

## Local start

Create `.env` from `.env.example`, replace the example passwords, and generate
the local keys once:

```bash
cp .env.example .env
bash scripts/gen_keys.sh
docker compose up -d --build --wait auth-service
```

The private key is mounted only in auth-service. Keep both generated files out
of Git; `.gitignore` already excludes PEM files and the `secrets/` directory.
Compose applies the schema through the one-shot `auth-migrate` service. On an
empty database, auth-service creates the first admin from `ADMIN_EMAIL` and
`ADMIN_PASSWORD`. Registration always creates a `client`, so it cannot grant
admin rights.

## API

| Method and path | Access | Purpose |
| --- | --- | --- |
| `POST /api/v1/auth/register` | Public | Create a client account |
| `POST /api/v1/auth/login` | Public | Check password and issue access/refresh tokens |
| `POST /api/v1/auth/refresh` | Public | Rotate a refresh token and issue a new pair |
| `POST /api/v1/auth/logout` | Bearer token | Revoke the supplied refresh token |
| `GET /api/v1/auth/me` | Bearer token | Return the current profile |
| `GET /api/v1/users` | Admin | List accounts, optionally filtered by role |
| `GET /api/v1/users/{id}` | Admin | Read an account profile |
| `PATCH /api/v1/users/{id}` | Admin | Change role, active state, or full name |

Access tokens use RS256, `iss=helpdesk-auth`, and a 15-minute default lifetime.
Refresh tokens last seven days, are stored by `jti`, and rotate on use. Reuse
of a revoked refresh token revokes the user's remaining refresh sessions.
Passwords use Argon2id. Five failed logins for one normalized email block that
email for 15 minutes; the response includes `Retry-After`.

Shared offline verification and `require_role("admin")` live in
`libs/common/helpdesk_common/auth.py`. Set `JWT_PUBLIC_KEY_PATH` in each
consumer service when the public key is mounted at a non-default location.
Nginx routing is added in the infrastructure PR.
