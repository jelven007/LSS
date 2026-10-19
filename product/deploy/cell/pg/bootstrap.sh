#!/usr/bin/env bash
set -Eeuo pipefail

: "${POSTGRES_USER:?POSTGRES_USER required}"
: "${POSTGRES_DB:?POSTGRES_DB required}"
: "${LSS_PG_RUNTIME_PASSWORD:?runtime password required}"

psql \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set=ON_ERROR_STOP=1 \
  --set=runtime_password="$LSS_PG_RUNTIME_PASSWORD" <<'SQL'
CREATE ROLE lss_runtime
  WITH LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE INHERIT
  PASSWORD :'runtime_password';

CREATE DATABASE lss_data_local OWNER lss_admin;

GRANT CONNECT ON DATABASE lss_control TO lss_runtime;
GRANT CONNECT ON DATABASE lss_data_local TO lss_runtime;
REVOKE CONNECT ON DATABASE lss_control FROM PUBLIC;
REVOKE CONNECT ON DATABASE lss_data_local FROM PUBLIC;
SQL
