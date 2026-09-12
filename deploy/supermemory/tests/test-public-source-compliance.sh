#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Ram0 contributors
# SPDX-License-Identifier: MIT

set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)

grep -Fq '# Ram0' "$ROOT/README.md"
grep -Fiq 'unofficial' "$ROOT/README.md"
grep -Fq 'supermemoryai/supermemory' "$ROOT/README.md"
grep -Fiq 'must remain private' "$ROOT/README.md"
grep -Fq 'Copyright (c) 2025 supermemory' "$ROOT/LICENSE"
grep -Fq 'It is not source code from this repository' "$ROOT/NOTICE"

if grep -Fq 'apps/web/public/logo-fullmark.svg' "$ROOT/README.md"; then
  echo 'README must not use the upstream logo as Ram0 branding' >&2
  exit 1
fi

for dockerfile in \
  "$ROOT/apps/local-gateway/Dockerfile" \
  "$ROOT/apps/memory-graph-playground/Dockerfile" \
  "$ROOT/deploy/supermemory/Dockerfile.engine"; do
  grep -Fq 'COPY LICENSE NOTICE /usr/share/licenses/ram0/' "$dockerfile"
done

echo 'public source compliance checks passed'
