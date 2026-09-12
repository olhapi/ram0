#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Ram0 contributors
# SPDX-License-Identifier: MIT

set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
COMPOSE="$ROOT/deploy/supermemory/compose.yaml"
DEPLOY="$ROOT/deploy/supermemory/deploy-unraid.sh"

[[ -f $COMPOSE ]]
[[ -f $DEPLOY ]]

grep -Fq '${RAM0_HOST_IP}:${RAM0_API_PORT:-18888}:8000' "$COMPOSE"
grep -Fq '${RAM0_HOST_IP}:${RAM0_GRAPH_PORT:-13000}:3000' "$COMPOSE"
grep -Fq '${RAM0_SUPERMEMORY_DATA_DIR}:/data' "$COMPOSE"
grep -Fq '127.0.0.1:28888' "$DEPLOY"
grep -Fq '127.0.0.1:23000' "$DEPLOY"
grep -Fq 'pg_dump' "$DEPLOY"
grep -Fq 'pg_restore --list' "$DEPLOY"
grep -Fq 'ram0_postgres' "$DEPLOY"

if grep -R -E 'docker\.sock|privileged:[[:space:]]*true|network_mode:[[:space:]]*host|compose down -v|docker compose down -v' \
  "$ROOT/deploy/supermemory" --exclude='verify-compose.sh'; then
  echo 'unsafe deployment setting found' >&2
  exit 1
fi

if grep -R -E 'rm[[:space:]].*(postgres|mem0)|/mnt/user/appdata/mem0/(postgres|history)' \
  "$ROOT/deploy/supermemory" --exclude='verify-compose.sh'; then
  echo 'legacy data deletion found' >&2
  exit 1
fi

echo 'deployment static checks passed'
