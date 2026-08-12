#!/usr/bin/env bash
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INPUT=$(cat)
printf '%s' "$INPUT" | "$SCRIPT_DIR/on_stop.sh" >/dev/null 2>&1 || true
printf '{}\n'
