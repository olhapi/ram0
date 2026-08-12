#!/usr/bin/env bash
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INPUT=$(cat)
printf '%s' "$INPUT" | python3 "$SCRIPT_DIR/on_pre_compact.py" >/dev/null 2>&1 || true
printf '{}\n'
