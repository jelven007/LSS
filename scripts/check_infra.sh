#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$PROJECT_ROOT/deploy/compose/.env"
COMPOSE_FILE="$PROJECT_ROOT/deploy/compose/docker-compose.yml"

if ! command -v docker >/dev/null 2>&1; then
  echo "error: docker is not installed" >&2
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "error: copy deploy/compose/.env.example to deploy/compose/.env first" >&2
  exit 1
fi

compose=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")

"${compose[@]}" ps

postgres_tables="$(
  "${compose[@]}" exec -T postgres sh -c \
    'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT count(*) FROM pg_tables WHERE schemaname = '\''banxia'\'';"'
)"
if (( postgres_tables < 14 )); then
  echo "error: expected at least 14 PostgreSQL tables, got $postgres_tables" >&2
  exit 1
fi

clickhouse_tables="$(
  "${compose[@]}" exec -T clickhouse sh -c \
    'clickhouse-client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" --query "SELECT count() FROM system.tables WHERE database = '\''banxia'\''"'
)"
if (( clickhouse_tables < 4 )); then
  echo "error: expected at least 4 ClickHouse tables/views, got $clickhouse_tables" >&2
  exit 1
fi

"${compose[@]}" exec -T redis sh -c \
  'test "$(redis-cli -a "$REDIS_PASSWORD" --no-auth-warning ping)" = "PONG"'

"${compose[@]}" run --rm minio-init

"${compose[@]}" exec -T api python -c \
  'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8765/api/v1/health/ready", timeout=5)'

"${compose[@]}" exec -T market-worker python -c \
  'import urllib.request; urllib.request.urlopen("http://127.0.0.1:9100/metrics", timeout=5)'

echo "Infrastructure smoke check passed."
