#!/bin/sh
set -eu

for migration in /policy-migrations/*.sql; do
  psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    --set=ON_ERROR_STOP=1 --file "$migration"
done

psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=ON_ERROR_STOP=1 \
  --set=control_password="$LSS_CONTROL_RUNTIME_PASSWORD" \
  --set=data_password="$LSS_DATA_RUNTIME_PASSWORD" <<'SQL'
CREATE ROLE lss_control_runtime
  WITH LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT
  PASSWORD :'control_password';
CREATE ROLE lss_data_runtime
  WITH LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT
  PASSWORD :'data_password';
CREATE DATABASE lss_control;
CREATE DATABASE lss_data_local;
REVOKE CONNECT ON DATABASE lss_control FROM PUBLIC;
REVOKE CONNECT ON DATABASE lss_data_local FROM PUBLIC;
GRANT CONNECT ON DATABASE lss_control TO lss_control_runtime;
GRANT CONNECT ON DATABASE lss_data_local TO lss_data_runtime;
SQL
