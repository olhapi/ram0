# SPDX-FileCopyrightText: 2026 Ram0 contributors
# SPDX-License-Identifier: Apache-2.0
"""Resolve a safe account-local project label from host and Git context."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from ram0_config import plugin_state_directory


APP_ID_MAX_LENGTH = 128
_HASH_SUFFIX_LENGTH = 16
_UNSAFE_APP_ID = re.compile(r"[^A-Za-z0-9._-]+")
_VALID_APP_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_SCP_REMOTE = re.compile(r"^(?:[^@/]+@)?(?P<host>\[[^]]+\]|[^:/]+):(?P<path>.+)$")
_MAPPING_NAME = "project_map.json"


class ProjectScopeError(ValueError):
    """A display-safe local project resolution failure."""


@dataclass(frozen=True, slots=True)
class ProjectContext:
    app_id: str
    root: Path
    branch: str


@dataclass(frozen=True, slots=True)
class _GitContext:
    root: Path
    common_directory: Path
    branch: str
    remote: str | None


def resolve_project_context(
    cwd: str | os.PathLike[str] | None = None,
    *,
    environment: Mapping[str, str] | None = None,
    state_dir: Path | None = None,
) -> ProjectContext:
    """Resolve one project context without exposing its path or remote outside this seam."""
    source = os.environ if environment is None else environment
    root = _resolve_cwd(cwd)
    git = _git_context(root)
    context_root = git.root if git is not None else root
    branch = git.branch if git is not None else "unknown"
    aliases = _alias_keys(git, context_root)
    mapping_path = (state_dir or plugin_state_directory(source)) / _MAPPING_NAME
    mapping = _read_mapping(mapping_path)

    explicit = source.get("RAM0_PROJECT_ID", "").strip()
    if explicit:
        app_id = _normalize_app_id(explicit)
    else:
        app_id = _mapped_app_id(mapping, aliases)
        if app_id is None and git is not None and git.remote is not None:
            canonical_remote = _canonical_remote(git.remote)
            if canonical_remote is not None:
                app_id = _normalize_app_id(canonical_remote.replace("/", "-"))
        if app_id is None:
            fallback_name = _repository_fallback_name(git) if git is not None else context_root.name
            app_id = _normalize_app_id(fallback_name)

    _save_mapping(mapping_path, mapping, aliases, app_id)
    return ProjectContext(app_id=app_id, root=context_root, branch=branch)


def _resolve_cwd(cwd: str | os.PathLike[str] | None) -> Path:
    if isinstance(cwd, str) and not cwd.strip():
        raise ProjectScopeError("Project context is unavailable.")
    try:
        candidate = Path.cwd() if cwd is None else Path(cwd).expanduser()
        resolved = candidate.resolve()
    except (OSError, RuntimeError, ValueError):
        raise ProjectScopeError("Project context is unavailable.") from None
    if not resolved.is_dir():
        raise ProjectScopeError("Project context is unavailable.")
    return resolved


def _run_git(cwd: Path, *arguments: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            shell=False,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    value = result.stdout.strip()
    return value or None


def _git_context(cwd: Path) -> _GitContext | None:
    raw_root = _run_git(cwd, "rev-parse", "--show-toplevel")
    if raw_root is None:
        return None
    try:
        root = Path(raw_root).resolve()
    except (OSError, RuntimeError):
        return None
    raw_common = _run_git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if raw_common is None:
        common_directory = root / ".git"
    else:
        common_candidate = Path(raw_common)
        if not common_candidate.is_absolute():
            common_candidate = root / common_candidate
        common_directory = common_candidate.resolve()
    return _GitContext(
        root=root,
        common_directory=common_directory,
        branch=_run_git(cwd, "branch", "--show-current") or "unknown",
        remote=_run_git(cwd, "config", "--get", "remote.origin.url"),
    )


def _canonical_remote(remote: str) -> str | None:
    candidate = remote.strip()
    if not candidate:
        return None
    if "://" in candidate:
        try:
            parsed = urlsplit(candidate)
            host = parsed.hostname
        except ValueError:
            return None
        if parsed.scheme.lower() == "file" or not host:
            return None
        path = parsed.path
    else:
        match = _SCP_REMOTE.fullmatch(candidate)
        if match is None:
            return None
        host = match.group("host").strip("[]")
        path = match.group("path").split("#", 1)[0].split("?", 1)[0]
    normalized_path = path.strip("/")
    if normalized_path.lower().endswith(".git"):
        normalized_path = normalized_path[:-4]
    if not normalized_path:
        return None
    return f"{host.lower()}/{normalized_path}"


def _normalize_app_id(value: str) -> str:
    normalized = _UNSAFE_APP_ID.sub("-", value.strip()).strip("-._")
    if not normalized or _VALID_APP_ID.fullmatch(normalized) is None:
        raise ProjectScopeError("Project context is unavailable.")
    if len(normalized) <= APP_ID_MAX_LENGTH:
        return normalized
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:_HASH_SUFFIX_LENGTH]
    prefix_length = APP_ID_MAX_LENGTH - _HASH_SUFFIX_LENGTH - 1
    prefix = normalized[:prefix_length].rstrip("-._")
    if not prefix:
        raise ProjectScopeError("Project context is unavailable.")
    return f"{prefix}-{digest}"


def _digest_alias(kind: str, value: str) -> str:
    return f"{kind}:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _alias_keys(git: _GitContext | None, root: Path) -> tuple[str, ...]:
    keys = [_digest_alias("root", os.fspath(git.common_directory if git is not None else root))]
    if git is not None and git.remote is not None:
        canonical_remote = _canonical_remote(git.remote)
        if canonical_remote is not None:
            keys.append(_digest_alias("remote", canonical_remote))
    return tuple(keys)


def _repository_fallback_name(git: _GitContext) -> str:
    if git.common_directory.name == ".git":
        return git.common_directory.parent.name
    return git.root.name


def _mapping_file_details(path: Path) -> os.stat_result:
    try:
        details = path.lstat()
    except FileNotFoundError:
        raise
    if not stat.S_ISREG(details.st_mode) or path.is_symlink():
        raise ProjectScopeError("Ram0 project mapping must be a regular file.")
    if os.name == "posix" and stat.S_IMODE(details.st_mode) & 0o077:
        raise ProjectScopeError("Ram0 project mapping permissions are unsafe; use mode 0600.")
    return details


def _assert_private_state_directory(path: Path) -> None:
    try:
        details = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(details.st_mode) or path.is_symlink():
        raise ProjectScopeError("Ram0 project state must be a regular directory.")
    if os.name == "posix" and stat.S_IMODE(details.st_mode) & 0o077:
        raise ProjectScopeError("Ram0 project state permissions are unsafe; use mode 0700.")


def _read_mapping(path: Path) -> dict[str, str]:
    _assert_private_state_directory(path.parent)
    try:
        _mapping_file_details(path)
    except FileNotFoundError:
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ProjectScopeError("Ram0 project mapping is invalid.") from None
    if not isinstance(raw, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in raw.items()
    ):
        raise ProjectScopeError("Ram0 project mapping is invalid.")
    return raw


def _mapped_app_id(mapping: Mapping[str, str], aliases: tuple[str, ...]) -> str | None:
    for alias in aliases:
        value = mapping.get(alias)
        if value is not None:
            return _normalize_app_id(value)
    return None


def _save_mapping(
    path: Path,
    existing: Mapping[str, str],
    aliases: tuple[str, ...],
    app_id: str,
) -> None:
    updated = dict(existing)
    updated.update(dict.fromkeys(aliases, app_id))
    if updated == existing:
        return
    directory = path.parent
    try:
        directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        _assert_private_state_directory(directory)
        try:
            _mapping_file_details(path)
        except FileNotFoundError:
            pass
        descriptor, raw_temporary_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=directory)
        temporary_path = Path(raw_temporary_path)
        try:
            os.fchmod(descriptor, 0o600)
            payload = (json.dumps(updated, indent=2, sort_keys=True) + "\n").encode("utf-8")
            os.write(descriptor, payload)
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            try:
                _mapping_file_details(path)
            except FileNotFoundError:
                pass
            os.replace(temporary_path, path)
            temporary_path = None
            if os.name == "posix":
                path.chmod(0o600)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    pass
    except ProjectScopeError:
        raise
    except OSError:
        raise ProjectScopeError("Unable to save Ram0 project mapping.") from None
