# SPDX-FileCopyrightText: 2026 Ram0 contributors
# SPDX-License-Identifier: Apache-2.0
"""Deterministic, allowlisted Ram0 marketplace export contracts."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from export_marketplace import ExportError, export_marketplace


REPOSITORY = Path(__file__).resolve().parents[3]
PLUGIN = REPOSITORY / "integrations" / "ram0-plugin"


def _files(root: Path) -> set[str]:
    return {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}


def test_export_is_bounded_complete_deterministic_and_preserves_modes(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()

    export_marketplace(REPOSITORY, first, "a" * 40)
    export_marketplace(REPOSITORY, second, "a" * 40)

    files = _files(first)
    assert files == _files(second)
    assert {path: (first / path).read_bytes() for path in files} == {
        path: (second / path).read_bytes() for path in files
    }
    assert {path: os.stat(first / path).st_mode & 0o777 for path in files} == {
        path: os.stat(second / path).st_mode & 0o777 for path in files
    }
    assert {path.parent.name for path in (first / "plugins/ram0/skills").glob("*/SKILL.md")} == {
        "ram0-memory", "remember", "forget", "peek", "tour", "health",
        "export", "import", "dream", "memory-reviewer", "stats", "onboard",
    }
    for required in (
        "marketplace.json",
        ".claude-plugin/marketplace.json",
        ".codex-plugin/marketplace.json",
        "plugins/ram0/.mcp.json",
        "plugins/ram0/.codex-mcp.json",
        "plugins/ram0/hooks/hooks.json",
        "plugins/ram0/hooks/codex-hooks.json",
        "plugins/ram0/bin/ram0",
        "plugins/ram0/scripts/mcp_stdio_adapter.py",
        "plugins/ram0/scripts/ram0_cli.py",
        "README.md",
        "LICENSE",
        "NOTICE",
        "source-manifest.json",
    ):
        assert required in files
    forbidden_parts = {".git", "node_modules", "__pycache__", "tests", "server"}
    assert not any(forbidden_parts.intersection(Path(path).parts) for path in files)
    assert not any(path.endswith((".db", ".sqlite", ".pyc")) or ".env" in path for path in files)
    assert "plugins/ram0/scripts/export_marketplace.py" not in files
    assert "plugins/ram0/scripts/publish_marketplace.py" not in files
    assert os.stat(first / "plugins/ram0/bin/ram0").st_mode & 0o111
    assert os.stat(first / "plugins/ram0/scripts/on_session_start.sh").st_mode & 0o111

    manifest = json.loads((first / "source-manifest.json").read_text())
    assert manifest["source_commit"] == "a" * 40
    assert manifest["plugin"] == "ram0"
    assert manifest["marketplace"] == "ram0-plugins"
    assert manifest["version"] == "0.1.0"
    assert list(manifest["files"]) == sorted(manifest["files"])
    assert "source-manifest.json" not in manifest["files"]


def test_export_rejects_nonempty_output_missing_source_and_escaping_symlink(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    (output / "existing").write_text("stop")
    with pytest.raises(ExportError, match="empty"):
        export_marketplace(REPOSITORY, output, "b" * 40)

    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ExportError, match="plugin source"):
        export_marketplace(tmp_path / "missing", empty, "b" * 40)

    source = tmp_path / "source"
    source.mkdir()
    plugin = source / "integrations/ram0-plugin"
    plugin.parent.mkdir()
    plugin.symlink_to(PLUGIN, target_is_directory=True)
    escaped = tmp_path / "escaped"
    escaped.mkdir()
    with pytest.raises(ExportError, match="symlink"):
        export_marketplace(source, escaped, "b" * 40)


def test_export_rejects_invalid_commit_and_manifest_version_disagreement(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    with pytest.raises(ExportError, match="commit"):
        export_marketplace(REPOSITORY, output, "main")

    source = tmp_path / "source"
    source.mkdir()
    plugin = source / "integrations/ram0-plugin"
    plugin.parent.mkdir()
    # A bounded fixture copy is sufficient to prove disagreement is checked before export.
    import shutil

    shutil.copytree(PLUGIN, plugin, ignore=shutil.ignore_patterns("node_modules", "dist", "__pycache__"))
    codex = json.loads((plugin / ".codex-plugin/plugin.json").read_text())
    codex["version"] = "9.9.9"
    (plugin / ".codex-plugin/plugin.json").write_text(json.dumps(codex))
    with pytest.raises(ExportError, match="versions"):
        export_marketplace(source, output, "c" * 40)
