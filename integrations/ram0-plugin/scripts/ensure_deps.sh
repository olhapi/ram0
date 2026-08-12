#!/usr/bin/env bash
# Ram0 automation is standard-library-only; this hook validates its local runtime.
set -uo pipefail
if ! command -v python3 >/dev/null 2>&1; then
  echo "Ram0 automation unavailable: install Python 3." >&2
fi
exit 0
