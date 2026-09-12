#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Ram0 contributors
# SPDX-License-Identifier: MIT

set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
TEST_DIR=$(mktemp -d)
trap 'rm -rf "$TEST_DIR"' EXIT
EVENTS=$TEST_DIR/events

# shellcheck source=../deploy-unraid.sh
source "$ROOT/deploy/supermemory/deploy-unraid.sh"

candidate_compose() { printf 'candidate:%s\n' "$*" >>"$EVENTS"; }
docker() { printf 'docker:%s\n' "$*" >>"$EVENTS"; }
live_compose() { printf 'live:%s\n' "$*" >>"$EVENTS"; }
wait_for_url() { printf 'wait:%s\n' "$1" >>"$EVENTS"; }
validate_write_freeze() { printf 'freeze:verified\n' >>"$EVENTS"; }

SCRIPT_DIR=$TEST_DIR
cat >"$SCRIPT_DIR/verify-stack.sh" <<'VERIFY'
#!/usr/bin/env bash
printf 'verify:%s:%s\n' "$1" "$2" >>"$EVENTS"
VERIFY
chmod 755 "$SCRIPT_DIR/verify-stack.sh"
export EVENTS

RAM0_HOST_IP=192.0.2.10
RAM0_RUNTIME_ENV_FILE=/private/runtime.env
RAM0_REVISION=0123456789abcdef0123456789abcdef01234567
RAM0_PUBLIC_API_URL=
RAM0_PUBLIC_GRAPH_URL=

promote >/dev/null

expected=$(cat <<'EXPECTED'
freeze:verified
candidate:stop graph gateway engine
docker:stop ram0_dashboard ram0_api
live:up -d --force-recreate engine gateway graph
wait:http://192.0.2.10:18888/health
wait:http://192.0.2.10:13000/
verify:http://192.0.2.10:18888:http://192.0.2.10:13000
EXPECTED
)
actual=$(cat "$EVENTS")
if [[ $actual != "$expected" ]]; then
  printf 'promotion did not wait for both live endpoints before verification\n' >&2
  exit 1
fi

printf 'promotion readiness test passed\n'
