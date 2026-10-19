# Cell PostgreSQL

This compose project provides one PostgreSQL instance with separate control and
Cell data databases:

- `lss_control`: users, memberships, routing, sessions, and strategy catalog.
- `lss_data_local`: tenant business data protected by forced RLS.

## Bootstrap

Create a root-only `.pg.env` with independent random passwords:

```dotenv
LSS_PG_ADMIN_PASSWORD=<random-admin-password>
LSS_PG_RUNTIME_PASSWORD=<random-runtime-password>
```

Start PostgreSQL:

```bash
set -a
. ./.pg.env
set +a
docker compose -p lss-pg -f compose.yml up -d
```

`bootstrap.sh` runs only for a new data volume. It creates `lss_data_local` and
the restricted `lss_runtime` role. The role is explicitly neither superuser nor
`BYPASSRLS`.

## Migrations

Run migrations with offline admin DSNs. Runtime containers must receive only
the `lss_runtime` DSNs.

```bash
python -m app.cell.admin migrate --kind control --runtime-role lss_runtime
python -m app.cell.admin migrate --kind data --runtime-role lss_runtime --cell-id local
```

PostgreSQL binds to `127.0.0.1:5432`. Cell containers connect through the
external Docker network `lss-pg_default`.
