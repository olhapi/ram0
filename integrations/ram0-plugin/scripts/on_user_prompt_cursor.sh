#!/usr/bin/env bash
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INPUT=$(cat)
TEXT=$(printf '%s' "$INPUT" | "$SCRIPT_DIR/on_user_prompt.sh" 2>/dev/null || true)
printf '%s' "$TEXT" | python3 -c 'import json,sys; text=sys.stdin.read(); value={"continue":True}; value.update({"user_message":text} if text else {}); print(json.dumps(value))'
