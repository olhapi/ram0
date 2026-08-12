#!/usr/bin/env bash
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INPUT=$(cat)
TEXT=$(printf '%s' "$INPUT" | "$SCRIPT_DIR/on_session_start.sh" 2>/dev/null || true)
printf '%s' "$TEXT" | python3 -c 'import json,sys; text=sys.stdin.read(); print(json.dumps({"additional_context":text} if text else {}))'
