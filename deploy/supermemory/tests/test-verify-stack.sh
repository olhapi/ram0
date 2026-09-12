#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Ram0 contributors
# SPDX-License-Identifier: MIT

set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
TEST_DIR=$(mktemp -d)
trap 'rm -rf "$TEST_DIR"' EXIT

cat >"$TEST_DIR/curl" <<'FAKE_CURL'
#!/usr/bin/env bash
set -euo pipefail

url=
data=
write_out=false
while (($#)); do
  case $1 in
    http://*|https://*) url=$1; shift ;;
    --data) data=$2; shift 2 ;;
    --write-out) write_out=true; shift 2 ;;
    --output) shift 2 ;;
    *) shift ;;
  esac
done

if [[ $write_out == true ]]; then
  printf '401'
elif [[ $url == */health ]]; then
  printf '{"status":"ok"}'
elif [[ $url == */v4/memories/list ]]; then
  if [[ $data != *'"containerTags":["personal"]'* ]]; then
    printf 'expected containerTags array in list request\n' >&2
    exit 22
  fi
  printf '{"memories":[],"pagination":{"currentPage":1,"totalItems":0,"totalPages":0}}'
elif [[ $url == */mcp && $data == *'"method":"initialize"'* ]]; then
  printf '{"protocolVersion":"2025-03-26"}'
elif [[ $url == */mcp && $data == *'"name":"who_am_i"'* ]]; then
  printf '{"content":[{"type":"text","text":"personal"}]}'
else
  printf '<html></html>'
fi
FAKE_CURL
chmod 755 "$TEST_DIR/curl"

printf 'SUPERMEMORY_API_KEY=test-only-key\n' >"$TEST_DIR/runtime.env"
chmod 600 "$TEST_DIR/runtime.env"

PATH="$TEST_DIR:$PATH" bash "$ROOT/deploy/supermemory/verify-stack.sh" \
  http://candidate-api http://candidate-graph "$TEST_DIR/runtime.env"

printf 'stack verification contract test passed\n'
