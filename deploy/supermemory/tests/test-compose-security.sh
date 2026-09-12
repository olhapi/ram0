#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Ram0 contributors
# SPDX-License-Identifier: MIT

set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
TEST_DIR=$(mktemp -d)
TMP_CONFIG=$TEST_DIR/compose.json
trap 'rm -rf "$TEST_DIR"' EXIT
printf 'SUPERMEMORY_API_KEY=test-only-key\n' >"$TEST_DIR/runtime.env"

digest=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
RAM0_ENGINE_IMAGE="example.invalid/engine@sha256:$digest" \
RAM0_GATEWAY_IMAGE="example.invalid/gateway@sha256:$digest" \
RAM0_GRAPH_IMAGE="example.invalid/graph@sha256:$digest" \
RAM0_HOST_IP=127.0.0.1 \
RAM0_SUPERMEMORY_DATA_DIR=/private/data \
RAM0_MIGRATION_DIR=/private/migration \
RAM0_RUNTIME_ENV_FILE="$TEST_DIR/runtime.env" \
OPENAI_API_KEY=test-only-key \
  docker compose -f "$ROOT/deploy/supermemory/compose.yaml" config --format json >"$TMP_CONFIG"

if ! jq -e '.services.engine.logging.driver == "none"' "$TMP_CONFIG" >/dev/null; then
  printf 'engine must suppress logs that contain its generated API key\n' >&2
  exit 1
fi

printf 'compose secret-log suppression test passed\n'
