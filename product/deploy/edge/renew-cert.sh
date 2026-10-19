#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

"$ROOT/issue-local-cert.sh"

docker exec lss-edge nginx -t
docker exec lss-edge nginx -s reload
