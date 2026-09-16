#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Ram0 contributors
# SPDX-License-Identifier: MIT

set -euo pipefail
umask 077

fail() {
  printf '[verify-supermemory] ERROR: %s\n' "$*" >&2
  exit 1
}

[[ $# -ge 3 && $# -le 5 ]] \
  || fail 'usage: verify-stack.sh <api-url> <graph-url> <runtime-env> [public-api-url] [public-graph-url]'

API_URL=${1%/}
GRAPH_URL=${2%/}
RUNTIME_ENV=$3
PUBLIC_API_URL=${4:-}
PUBLIC_GRAPH_URL=${5:-}

[[ -f $RUNTIME_ENV ]] || fail "runtime environment is missing: $RUNTIME_ENV"
[[ $(stat -c '%a' "$RUNTIME_ENV") == 600 ]] || fail 'runtime environment must be mode 600'

# shellcheck disable=SC1090
source "$RUNTIME_ENV"
[[ -n ${SUPERMEMORY_API_KEY:-} ]] || fail 'SUPERMEMORY_API_KEY is missing'

TMP_RESPONSE=$(mktemp)
trap 'rm -f "$TMP_RESPONSE"' EXIT

curl --fail --silent --show-error --max-time 10 "$API_URL/health" >"$TMP_RESPONSE"
grep -Fq '"status":"ok"' "$TMP_RESPONSE" || fail 'gateway health response is degraded'

STATUS=$(curl --silent --output /dev/null --write-out '%{http_code}' --max-time 10 "$API_URL/")
[[ $STATUS == 401 ]] || fail "unauthenticated gateway request returned $STATUS instead of 401"

curl --fail --silent --show-error --max-time 15 \
  -H "Authorization: Bearer $SUPERMEMORY_API_KEY" \
  -H 'Content-Type: application/json' \
  -X POST "$API_URL/v4/memories/list" \
  --data '{"containerTags":["personal"],"page":1,"limit":20,"sort":"createdAt","order":"desc"}' >"$TMP_RESPONSE"

curl --fail --silent --show-error --max-time 15 \
  -H "Authorization: Bearer $SUPERMEMORY_API_KEY" \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  -X POST "$API_URL/mcp" \
  --data '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"deployment-check","version":"1"}}}' \
  >"$TMP_RESPONSE"
grep -Fq 'protocolVersion' "$TMP_RESPONSE" || fail 'MCP initialize response is invalid'

curl --fail --silent --show-error --max-time 15 \
  -H "Authorization: Bearer $SUPERMEMORY_API_KEY" \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  -X POST "$API_URL/mcp" \
  --data '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"who_am_i","arguments":{}}}' \
  >"$TMP_RESPONSE"
grep -Fq 'personal' "$TMP_RESPONSE" || fail 'MCP tool invocation failed'

# Accepted writes are not enough: a broken workflow engine leaves every document queued.
curl --fail --silent --show-error --max-time 15 \
  -H "Authorization: Bearer $SUPERMEMORY_API_KEY" \
  -H 'Content-Type: application/json' \
  -X POST "$API_URL/v3/documents" \
  --data "{\"content\":\"Deployment ingestion check at $(date -u +%Y-%m-%dT%H:%M:%SZ): the operator prefers verified releases.\",\"containerTag\":\"ram0_deploy_check\",\"customId\":\"ram0-deploy-check\"}" \
  >"$TMP_RESPONSE"
CHECK_ID=$(grep -o '"id":"[^"]*"' "$TMP_RESPONSE" | head -1 | cut -d'"' -f4)
[[ -n $CHECK_ID ]] || fail 'ingestion check document was not accepted'
CHECK_STATUS=
for ((attempt = 0; attempt < ${VERIFY_INGEST_ATTEMPTS:-60}; attempt++)); do
  curl --fail --silent --show-error --max-time 15 \
    -H "Authorization: Bearer $SUPERMEMORY_API_KEY" \
    "$API_URL/v3/documents/$CHECK_ID" >"$TMP_RESPONSE"
  CHECK_STATUS=$(grep -o '"status":"[^"]*"' "$TMP_RESPONSE" | head -1 | cut -d'"' -f4)
  [[ $CHECK_STATUS == done || $CHECK_STATUS == failed ]] && break
  sleep "${VERIFY_INGEST_INTERVAL:-5}"
done
curl --silent --output /dev/null --max-time 15 \
  -H "Authorization: Bearer $SUPERMEMORY_API_KEY" \
  -X DELETE "$API_URL/v3/documents/$CHECK_ID" || true
[[ $CHECK_STATUS == done ]] || fail "ingestion check document ended as '${CHECK_STATUS:-unknown}' instead of done"

curl --fail --silent --show-error --max-time 15 "$GRAPH_URL/" >/dev/null

if [[ -n $PUBLIC_API_URL ]]; then
  curl --fail --silent --show-error --max-time 20 "${PUBLIC_API_URL%/}/health" >/dev/null
fi
if [[ -n $PUBLIC_GRAPH_URL ]]; then
  curl --fail --silent --show-error --max-time 20 "${PUBLIC_GRAPH_URL%/}/" >/dev/null
fi

printf '[verify-supermemory] REST, MCP, ingestion, graph, and configured public checks passed\n'
