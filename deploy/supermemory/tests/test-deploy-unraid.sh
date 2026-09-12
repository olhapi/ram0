#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Ram0 contributors
# SPDX-License-Identifier: MIT

set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)

docker() {
  [[ $1 == inspect && $2 == --format && $4 == ram0_api ]]
  printf '%s\n' \
    'POSTGRES_DB=postgres' \
    'APP_DB_NAME=mem0_app' \
    'POSTGRES_USER=postgres'
}

# shellcheck source=../deploy-unraid.sh
source "$ROOT/deploy/supermemory/deploy-unraid.sh"

actual=$(legacy_application_database)
[[ $actual == mem0_app ]]

printf 'deployment database-resolution test passed\n'
