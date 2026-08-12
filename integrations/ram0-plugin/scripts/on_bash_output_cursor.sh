#!/usr/bin/env bash
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INPUT=$(cat)
TEXT=$(printf '%s' "$INPUT" | "$SCRIPT_DIR/on_bash_output.sh" 2>/dev/null || true)
printf '%s' "$TEXT" | python3 -c 'import json,sys; text=sys.stdin.read(); value={"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":text}} if text else {}; print(json.dumps(value))'
