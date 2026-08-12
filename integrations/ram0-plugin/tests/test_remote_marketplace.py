# SPDX-FileCopyrightText: 2026 Ram0 contributors
# SPDX-License-Identifier: Apache-2.0
"""Real host-CLI installation and native Git marketplace update contracts."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest

from export_marketplace import export_marketplace


REPOSITORY = Path(__file__).resolve().parents[3]


class GitHttpServer:
    def __init__(self, root: Path):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self._git()

            def do_POST(self):
                self._git()

            def log_message(self, _format, *_args):
                return

            def _git(self):
                parsed = urlsplit(self.path)
                length = int(self.headers.get("Content-Length", "0"))
                environment = {
                    **os.environ,
                    "GIT_HTTP_EXPORT_ALL": "1",
                    "GIT_PROJECT_ROOT": str(root),
                    "PATH_INFO": parsed.path,
                    "QUERY_STRING": parsed.query,
                    "REQUEST_METHOD": self.command,
                    "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                    "CONTENT_LENGTH": str(length),
                    "REMOTE_ADDR": "127.0.0.1",
                }
                result = subprocess.run(
                    ["git", "http-backend"],
                    input=self.rfile.read(length),
                    capture_output=True,
                    env=environment,
                    check=True,
                )
                headers, body = result.stdout.split(b"\r\n\r\n", 1)
                status = 200
                parsed_headers = []
                for line in headers.split(b"\r\n"):
                    name, value = line.decode().split(":", 1)
                    if name.lower() == "status":
                        status = int(value.strip().split(" ", 1)[0])
                    else:
                        parsed_headers.append((name, value.strip()))
                self.send_response(status)
                for name, value in parsed_headers:
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return f"http://127.0.0.1:{self.server.server_port}/market.git"

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


def _run(arguments: list[str], *, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(arguments, check=True, capture_output=True, text=True, env=environment, timeout=120)


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    work = tmp_path / "work"
    work.mkdir()
    export_marketplace(REPOSITORY, work, "1" * 40)
    (work / "plugins/ram0/VERSION_MARKER").write_text("v1\n")
    _run(["git", "init", "-b", "main"], environment={**os.environ, "GIT_DIR": str(work / ".git"), "GIT_WORK_TREE": str(work)})
    _run(["git", "-C", str(work), "add", "."], environment=os.environ.copy())
    _run(
        ["git", "-C", str(work), "-c", "commit.gpgsign=false", "-c", "user.name=Ram0", "-c", "user.email=ram0@example.invalid", "commit", "-m", "v1"],
        environment=os.environ.copy(),
    )
    bare = tmp_path / "served/market.git"
    bare.parent.mkdir()
    _run(["git", "clone", "--bare", str(work), str(bare)], environment=os.environ.copy())
    return work, bare


def _publish_v2(work: Path, bare: Path) -> None:
    (work / "plugins/ram0/VERSION_MARKER").write_text("v2\n")
    for relative in (
        "plugins/ram0/.claude-plugin/plugin.json",
        "plugins/ram0/.codex-plugin/plugin.json",
        ".claude-plugin/marketplace.json",
    ):
        path = work / relative
        document = json.loads(path.read_text())
        if relative.startswith("plugins/"):
            document["version"] = "0.1.1"
        else:
            document["plugins"][0]["version"] = "0.1.1"
        path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    _run(["git", "-C", str(work), "add", "."], environment=os.environ.copy())
    _run(
        ["git", "-C", str(work), "-c", "commit.gpgsign=false", "-c", "user.name=Ram0", "-c", "user.email=ram0@example.invalid", "commit", "-m", "v2"],
        environment=os.environ.copy(),
    )
    _run(["git", "-C", str(work), "push", str(bare), "main"], environment=os.environ.copy())


def test_codex_marketplace_upgrade_refreshes_installed_plugin(tmp_path):
    codex = shutil.which("codex")
    if codex is None:
        pytest.skip("Codex CLI is unavailable")
    work, bare = _fixture(tmp_path)
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    environment = {**os.environ, "CODEX_HOME": str(codex_home)}

    with GitHttpServer(bare.parent) as url:
        _run([codex, "plugin", "marketplace", "add", url], environment=environment)
        installed = json.loads(_run([codex, "plugin", "add", "ram0@ram0-plugins", "--json"], environment=environment).stdout)
        installed_path = Path(installed["installedPath"])
        assert (installed_path / "VERSION_MARKER").read_text() == "v1\n"
        _publish_v2(work, bare)
        _run([codex, "plugin", "marketplace", "upgrade", "ram0-plugins", "--json"], environment=environment)

    assert (installed_path.parent / "0.1.1/VERSION_MARKER").read_text() == "v2\n"


def test_claude_native_update_commands_refresh_installed_plugin(tmp_path):
    claude = shutil.which("claude")
    if claude is None:
        pytest.skip("Claude Code CLI is unavailable")
    work, bare = _fixture(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    environment = {**os.environ, "HOME": str(home)}

    with GitHttpServer(bare.parent) as url:
        _run([claude, "plugin", "marketplace", "add", url], environment=environment)
        _run([claude, "plugin", "install", "ram0@ram0-plugins"], environment=environment)
        _publish_v2(work, bare)
        _run([claude, "plugin", "marketplace", "update", "ram0-plugins"], environment=environment)
        _run([claude, "plugin", "update", "ram0@ram0-plugins"], environment=environment)

    markers = list((home / ".claude/plugins/cache/ram0-plugins/ram0").glob("*/VERSION_MARKER"))
    assert any(path.read_text() == "v2\n" for path in markers)
