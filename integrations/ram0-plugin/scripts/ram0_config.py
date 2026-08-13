# SPDX-FileCopyrightText: 2026 Ram0 contributors
# SPDX-License-Identifier: Apache-2.0
"""Protected persistent configuration shared by Ram0 plugin components."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

RAM0_USER_AGENT = "ram0-plugin/0.1.2"
from urllib.parse import urlsplit, urlunsplit


DEFAULT_RAM0_API_URL = "http://localhost:8888"
CONFIG_RELATIVE_PATH = Path(".config/ram0/config.json")
PLUGIN_STATE_RELATIVE_PATH = Path(".ram0/plugin-data")


class Ram0ConfigError(ValueError):
    """A display-safe local configuration failure."""


@dataclass(frozen=True)
class Ram0Config:
    api_url: str
    api_key: str | None = field(repr=False)

    def display(self) -> dict[str, str | bool]:
        return {"api_url": self.api_url, "api_key_configured": self.api_key is not None}


def config_path(home: Path | None = None) -> Path:
    return (Path.home() if home is None else Path(home)) / CONFIG_RELATIVE_PATH


def plugin_state_directory(
    environment: Mapping[str, str] | None = None,
    *,
    home: Path | None = None,
) -> Path:
    """Return the host-configured private state directory for plugin runtime data."""
    source = os.environ if environment is None else environment
    configured = source.get("RAM0_PLUGIN_DATA") or source.get("CLAUDE_PLUGIN_DATA")
    if configured:
        return Path(configured).expanduser()
    return (Path.home() if home is None else Path(home)) / PLUGIN_STATE_RELATIVE_PATH


def normalize_api_url(value: str) -> str:
    candidate = value.strip()
    parsed = urlsplit(candidate)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise Ram0ConfigError("RAM0 API URL must be an absolute HTTP(S) URL without credentials, query, or fragment.")
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _assert_regular_private_file(path: Path) -> os.stat_result:
    try:
        details = path.lstat()
    except FileNotFoundError:
        raise
    if not stat.S_ISREG(details.st_mode) or path.is_symlink():
        raise Ram0ConfigError(f"Ram0 config must be a regular file: {path}")
    if os.name == "posix" and stat.S_IMODE(details.st_mode) & 0o077:
        raise Ram0ConfigError(f"Ram0 config permissions are unsafe; run `chmod 600 {path}`.")
    return details


def _assert_private_directory(path: Path) -> None:
    try:
        details = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(details.st_mode) or path.is_symlink():
        raise Ram0ConfigError(f"Ram0 config directory must be a regular directory: {path}")
    if os.name == "posix" and stat.S_IMODE(details.st_mode) & 0o077:
        raise Ram0ConfigError(f"Ram0 config directory permissions are unsafe; run `chmod 700 {path}`.")


def _read_stored(path: Path) -> dict[str, Any]:
    _assert_private_directory(path.parent)
    try:
        _assert_regular_private_file(path)
    except FileNotFoundError:
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise Ram0ConfigError(f"Ram0 config is not valid JSON: {path}") from error
    if not isinstance(value, dict):
        raise Ram0ConfigError(f"Ram0 config must contain a JSON object: {path}")
    return value


def load_config(
    environment: Mapping[str, str] | None = None,
    *,
    home: Path | None = None,
    require_key: bool = False,
) -> Ram0Config:
    source = os.environ if environment is None else environment
    stored = _read_stored(config_path(home))
    raw_url = source.get("RAM0_API_URL") or stored.get("api_url") or DEFAULT_RAM0_API_URL
    if not isinstance(raw_url, str):
        raise Ram0ConfigError("Ram0 config api_url must be a string.")
    raw_key = source.get("RAM0_API_KEY") or stored.get("api_key") or ""
    if not isinstance(raw_key, str):
        raise Ram0ConfigError("Ram0 config api_key must be a string.")
    api_key = raw_key.strip() or None
    if require_key and api_key is None:
        raise Ram0ConfigError("Ram0 API key is missing; run `ram0 setup`.")
    return Ram0Config(normalize_api_url(raw_url), api_key)


def _validate_destination(path: Path) -> None:
    try:
        _assert_regular_private_file(path)
    except FileNotFoundError:
        return


def write_config(api_url: str, api_key: str, *, home: Path | None = None) -> Path:
    normalized_url = normalize_api_url(api_url)
    normalized_key = api_key.strip()
    if not normalized_key:
        raise Ram0ConfigError("Ram0 API key must not be blank.")
    path = config_path(home)
    directory = path.parent
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    _assert_private_directory(directory)
    _validate_destination(path)

    descriptor = -1
    temporary_path: Path | None = None
    try:
        descriptor, raw_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=directory)
        temporary_path = Path(raw_path)
        os.fchmod(descriptor, 0o600)
        payload = (json.dumps({"api_url": normalized_url, "api_key": normalized_key}, indent=2) + "\n").encode()
        os.write(descriptor, payload)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        _validate_destination(path)
        os.replace(temporary_path, path)
        temporary_path = None
        if os.name == "posix":
            path.chmod(0o600)
        return path
    except OSError as error:
        raise Ram0ConfigError(f"Unable to write Ram0 config: {path}") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def update_config(
    *,
    api_url: str | None = None,
    api_key: str | None = None,
    home: Path | None = None,
) -> Path:
    current = load_config({}, home=home)
    selected_url = current.api_url if api_url is None else api_url
    selected_key = current.api_key if api_key is None else api_key
    if selected_key is None:
        raise Ram0ConfigError("Ram0 API key is missing; run `ram0 setup`.")
    return write_config(selected_url, selected_key, home=home)
