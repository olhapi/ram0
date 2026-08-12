#!/usr/bin/env python3
"""Install the bounded Ram0 configuration CLI into the current user profile."""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TextIO


SOURCE_DIR = Path(__file__).resolve().parent
LAUNCHER = SOURCE_DIR.parent / "bin" / "ram0"
RUNTIME_FILES = ("mcp_stdio_adapter.py", "ram0_cli.py", "ram0_config.py")


def _safe_directory(path: Path, mode: int) -> None:
    if path.is_symlink():
        raise OSError(f"Ram0 install directory must be a regular directory: {path}")
    path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir() or path.is_symlink():
        raise OSError(f"Ram0 install directory must be a regular directory: {path}")
    path.chmod(mode)


def _atomic_install(source: Path, destination: Path, mode: int) -> bool:
    payload = source.read_bytes()
    if destination.is_symlink() or (destination.exists() and not destination.is_file()):
        raise OSError(f"Ram0 install destination must be a regular file: {destination}")
    if destination.exists() and destination.read_bytes() == payload and destination.stat().st_mode & 0o777 == mode:
        return False
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(mode)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def install(
    *,
    home: Path | None = None,
    environment: Mapping[str, str] | None = None,
    stdout: TextIO | None = None,
    quiet: bool = False,
) -> Path:
    user_home = Path.home() if home is None else Path(home)
    source_environment = os.environ if environment is None else environment
    output = sys.stdout if stdout is None else stdout
    share = user_home / ".local/share/ram0"
    binary_directory = user_home / ".local/bin"
    _safe_directory(share, 0o700)
    _safe_directory(binary_directory, 0o755)
    changed = False
    for name in RUNTIME_FILES:
        destination = share / name
        changed = _atomic_install(SOURCE_DIR / name, destination, 0o600) or changed
    executable = binary_directory / "ram0"
    changed = _atomic_install(LAUNCHER, executable, 0o755) or changed
    if changed and not quiet:
        print(f"Installed Ram0 CLI: {executable}", file=output)
    path_entries = source_environment.get("PATH", "").split(os.pathsep)
    if changed and not quiet and str(binary_directory) not in path_entries:
        print(f"Run `{executable} setup` (the directory is not currently on PATH).", file=output)
    return executable


def main(argv: Sequence[str] | None = None, *, home: Path | None = None) -> int:
    if argv:
        raise SystemExit("install_cli.py takes no arguments")
    install(home=home)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
