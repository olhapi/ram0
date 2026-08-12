#!/usr/bin/env python3
"""Store only explicit durable continuation facts before compaction."""

from __future__ import annotations

import sys

from memory_capture import main


if __name__ == "__main__":
    sys.argv = [sys.argv[0], "capture", "--source", "precompact"]
    raise SystemExit(main())
