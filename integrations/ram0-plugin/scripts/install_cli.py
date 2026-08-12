#!/usr/bin/env python3
"""Install the bounded Ram0 configuration CLI into the current user profile."""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TextIO


SOURCE_DIR = Path(__file__).resolve().parent
LAUNCHER = SOURCE_DIR.parent / "bin" / "ram0"
RUNTIME_FILES = ("mcp_stdio_adapter.py", "ram0_cli.py", "ram0_config.py")


def install(
    *,
    home: Path | None = None,
    environment: Mapping[str, str] | None = None,
    stdout: TextIO | None = None,
) -> Path:
    user_home = Path.home() if home is None else Path(home)
    source_environment = os.environ if environment is None else environment
    output = sys.stdout if stdout is None else stdout
    share = user_home / ".local/share/ram0"
    binary_directory = user_home / ".local/bin"
    share.mkdir(parents=True, exist_ok=True)
    binary_directory.mkdir(parents=True, exist_ok=True)
    for name in RUNTIME_FILES:
        destination = share / name
        shutil.copyfile(SOURCE_DIR / name, destination)
        destination.chmod(0o600)
    executable = binary_directory / "ram0"
    shutil.copyfile(LAUNCHER, executable)
    executable.chmod(0o755)
    print(f"Installed Ram0 CLI: {executable}", file=output)
    path_entries = source_environment.get("PATH", "").split(os.pathsep)
    if str(binary_directory) not in path_entries:
        print(f"Run `{executable} setup` (the directory is not currently on PATH).", file=output)
    return executable


def main(argv: Sequence[str] | None = None, *, home: Path | None = None) -> int:
    if argv:
        raise SystemExit("install_cli.py takes no arguments")
    install(home=home)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
