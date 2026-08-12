#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Ram0 contributors
# SPDX-License-Identifier: Apache-2.0
"""Idempotently install the bounded Ram0 CLI runtime from the active plugin."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TextIO

from install_cli import RUNTIME_FILES, install


def _snapshot(home: Path) -> dict[Path, bytes | None]:
    paths = [home / ".local/share/ram0" / name for name in RUNTIME_FILES]
    paths.append(home / ".local/bin/ram0")
    return {
        path: path.read_bytes() if path.is_file() and not path.is_symlink() else None
        for path in paths
    }


def bootstrap(
    *,
    home: Path | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> bool:
    user_home = Path.home() if home is None else Path(home)
    prior = _snapshot(user_home)
    install(home=user_home, stdout=stdout, quiet=True)
    return prior != _snapshot(user_home)


def main() -> int:
    try:
        changed = bootstrap(stdout=sys.stdout, stderr=sys.stderr)
    except OSError:
        print("Ram0 CLI bootstrap failed; run the bundled install_cli.py manually.", file=sys.stderr)
        return 1
    if changed:
        print("Ram0 CLI installed from the active plugin bundle.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
