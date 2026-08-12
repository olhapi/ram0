#!/usr/bin/env bash
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
python3 "$SCRIPT_DIR/bootstrap_cli.py" >/dev/null || true
exec python3 "$SCRIPT_DIR/memory_capture.py" search --purpose session
