#!/bin/sh
set -eu

for migration in /policy-migrations/*.sql; do
  psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    --set=ON_ERROR_STOP=1 --file "$migration"
done

psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=ON_ERROR_STOP=1 \
  --set=control_password="$TSP_CONTROL_RUNTIME_PASSWORD" \
  --set=data_password="$TSP_DATA_RUNTIME_PASSWORD" <<'SQL'
CREATE ROLE tsp_control_runtime
  WITH LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT
  PASSWORD :'control_password';
CREATE ROLE tsp_data_runtime
  WITH LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT
  PASSWORD :'data_password';
CREATE DATABASE tsp_control;
CREATE DATABASE tsp_data_local;
REVOKE CONNECT ON DATABASE tsp_control FROM PUBLIC;
REVOKE CONNECT ON DATABASE tsp_data_local FROM PUBLIC;
GRANT CONNECT ON DATABASE tsp_control TO tsp_control_runtime;
GRANT CONNECT ON DATABASE tsp_data_local TO tsp_data_runtime;
SQL
