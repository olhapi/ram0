#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Ram0 contributors
# SPDX-License-Identifier: Apache-2.0
"""Export the canonical Ram0 plugin as a small deterministic marketplace."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
from pathlib import Path


class ExportError(ValueError):
    """The requested marketplace export is unsafe or inconsistent."""


PLUGIN_FILES = (
    ".claude-plugin/plugin.json",
    ".codex-plugin/plugin.json",
    ".cursor-plugin/plugin.json",
    ".codex-mcp.json",
    ".cursor-mcp.json",
    ".mcp.json",
    "README.md",
    "UPSTREAM.md",
    "mcp_config.json",
    "plugin.json",
    "requirements.txt",
)
PLUGIN_TREES = ("bin", "hooks", "scripts", "skills")
EXCLUDED_SUFFIXES = (".pyc", ".db", ".sqlite", ".sqlite3")
SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"(?:api[_-]?key|token|password)\s*[:=]\s*['\"][A-Za-z0-9_./+\-=]{20,}['\"]", re.I),
)


def _json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ExportError(f"Invalid required JSON file: {path}") from error
    if not isinstance(value, dict):
        raise ExportError(f"Required JSON object missing: {path}")
    return value


def _assert_safe_source(path: Path, root: Path) -> None:
    if path.is_symlink():
        raise ExportError(f"Marketplace export rejects symlink source: {path}")
    try:
        path.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (FileNotFoundError, ValueError) as error:
        raise ExportError(f"Required plugin source is missing or escapes its root: {path}") from error


def _copy(source: Path, destination: Path, root: Path) -> None:
    _assert_safe_source(source, root)
    if not source.is_file():
        raise ExportError(f"Required plugin source is not a file: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _tree_files(root: Path, name: str) -> list[Path]:
    tree = root / name
    _assert_safe_source(tree, root)
    result = []
    for path in sorted(tree.rglob("*")):
        if path.is_symlink():
            raise ExportError(f"Marketplace export rejects symlink source: {path}")
        if path.is_file() and "__pycache__" not in path.parts and not path.name.endswith(EXCLUDED_SUFFIXES):
            result.append(path)
    return result


def _marketplace_documents(version: str) -> dict[str, dict]:
    codex = {
        "name": "ram0-plugins",
        "interface": {"displayName": "Ram0 Plugins"},
        "plugins": [{
            "name": "ram0",
            "source": {"source": "local", "path": "./plugins/ram0"},
            "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
            "category": "Productivity",
        }],
    }
    claude = {
        "name": "ram0-plugins",
        "owner": {"name": "Ram0"},
        "metadata": {"description": "Self-hosted Ram0 plugins"},
        "plugins": [{
            "name": "ram0",
            "source": "./plugins/ram0",
            "description": "Self-hosted Ram0 memory with account-derived bearer authentication.",
            "version": version,
        }],
    }
    return {"marketplace.json": codex, ".codex-plugin/marketplace.json": codex, ".claude-plugin/marketplace.json": claude}


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _scan_output(output: Path) -> None:
    forbidden_names = {".env", "config.json", "credentials.json", "id_rsa", "id_ed25519"}
    for path in output.rglob("*"):
        if path.is_symlink():
            raise ExportError(f"Generated marketplace contains a symlink: {path}")
        if not path.is_file():
            continue
        if path.name in forbidden_names or path.name.endswith(EXCLUDED_SUFFIXES):
            raise ExportError(f"Generated marketplace contains forbidden file: {path.name}")
        data = path.read_bytes()
        if any(pattern.search(data) for pattern in SECRET_PATTERNS):
            raise ExportError(f"Generated marketplace contains secret-shaped content: {path}")


def export_marketplace(source_root: Path, output_root: Path, source_commit: str) -> dict[str, object]:
    source_root = Path(source_root)
    output_root = Path(output_root)
    if not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        raise ExportError("Source commit must be a 40-character lowercase hexadecimal SHA.")
    if not output_root.is_dir() or any(output_root.iterdir()):
        raise ExportError("Marketplace output directory must exist and be empty.")
    plugin = source_root / "integrations/ram0-plugin"
    if not plugin.is_dir() or plugin.is_symlink():
        raise ExportError("Canonical plugin source directory is missing or is a symlink.")

    manifests = [_json(plugin / name) for name in (".claude-plugin/plugin.json", ".codex-plugin/plugin.json")]
    names = {item.get("name") for item in manifests}
    versions = {item.get("version") for item in manifests}
    if names != {"ram0"}:
        raise ExportError("Plugin manifest names disagree.")
    if len(versions) != 1 or not next(iter(versions), None):
        raise ExportError("Plugin manifest versions disagree.")
    version = str(next(iter(versions)))

    destination = output_root / "plugins/ram0"
    for relative in PLUGIN_FILES:
        _copy(plugin / relative, destination / relative, plugin)
    for tree in PLUGIN_TREES:
        for source in _tree_files(plugin, tree):
            _copy(source, destination / source.relative_to(plugin), plugin)
    _copy(plugin / "distribution/README.md", output_root / "README.md", plugin)
    _copy(source_root / "LICENSE", output_root / "LICENSE", source_root)
    _copy(source_root / "NOTICE", output_root / "NOTICE", source_root)
    for relative, document in _marketplace_documents(version).items():
        _write_json(output_root / relative, document)

    _scan_output(output_root)
    hashes = {
        path.relative_to(output_root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(output_root.rglob("*"))
        if path.is_file()
    }
    manifest: dict[str, object] = {
        "files": hashes,
        "marketplace": "ram0-plugins",
        "plugin": "ram0",
        "source_commit": source_commit,
        "version": version,
    }
    _write_json(output_root / "source-manifest.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    if not args.output.is_absolute():
        parser.error("--output must be an absolute path")
    repository = Path(__file__).resolve().parents[3]
    manifest = export_marketplace(repository, args.output, args.source_commit)
    print(f"Exported {len(manifest['files'])} files for ram0 {manifest['version']} from {args.source_commit}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
