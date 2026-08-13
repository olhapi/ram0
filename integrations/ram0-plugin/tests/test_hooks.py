"""Installed lifecycle contract tests for Claude, Codex, and Cursor."""

# Modified for Ram0; see NOTICE and repository history.

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from memory_capture import AUTOMATIC_CONTEXT_VERSION, _automatic_context_proof
from test_workflow_skills import EXPECTED_WORKFLOW_SKILLS


ROOT = Path(__file__).resolve().parents[1]


def _trusted_memory(text: str, key: str = "ram0-test-key") -> dict:
    return {
        "memory": text,
        "metadata": {
            "ram0_auto_context_version": AUTOMATIC_CONTEXT_VERSION,
            "ram0_auto_context_proof": _automatic_context_proof(key, text),
        },
    }


def _commands(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "command" and isinstance(child, str):
                yield child
            yield from _commands(child)
    elif isinstance(value, list):
        for child in value:
            yield from _commands(child)


def test_manifests_install_equivalent_safe_default_lifecycle():
    """Breaks if a supported host loses retrieval/capture or gains an upstream/vendor hook."""
    manifests = {
        name: json.loads((ROOT / "hooks" / name).read_text())
        for name in ("hooks.json", "codex-hooks.json", "cursor-hooks.json")
    }

    for name, manifest in manifests.items():
        source = json.dumps(manifest)
        commands = list(_commands(manifest))
        assert "on_session_start" in source
        assert "on_user_prompt" in source
        assert "on_bash_output" in source
        assert "on_pre_compact" in source
        assert "on_stop" in source
        assert "startup|resume|compact" in source
        assert commands and all("ram0-plugin" in command.lower() or "RAM0_PLATFORM" in command for command in commands)
        assert "mcp__mem0" not in source.lower()
        assert "api.mem0.ai" not in source.lower()
        assert "mcp.mem0.ai" not in source.lower()


def test_hook_entrypoints_exist_and_are_executable():
    """Breaks if an installed manifest points at a missing or non-executable lifecycle entrypoint."""
    for name in ("on_session_start.sh", "on_user_prompt.sh", "on_bash_output.sh", "on_stop.sh", "ensure_deps.sh"):
        path = ROOT / "scripts" / name
        assert path.is_file()
        assert path.stat().st_mode & 0o111
    assert (ROOT / "scripts" / "on_pre_compact.py").is_file()


def test_cursor_package_manifest_wires_cursor_hooks():
    """Breaks if Cursor cannot discover the Ram0 package or its host-specific wrappers."""
    plugin = json.loads((ROOT / ".cursor-plugin" / "plugin.json").read_text())
    assert plugin["name"] == "ram0"
    assert plugin["hooks"] == "./hooks/cursor-hooks.json"
    source = (ROOT / "hooks" / "cursor-hooks.json").read_text()
    for name in (
        "on_session_start_cursor.sh",
        "on_user_prompt_cursor.sh",
        "on_bash_output_cursor.sh",
        "on_pre_compact_cursor.sh",
        "on_stop_cursor.sh",
    ):
        assert name in source
        assert (ROOT / "scripts" / name).stat().st_mode & 0o111


def test_supported_client_manifests_launch_the_same_config_aware_stdio_adapter():
    """Breaks if a marketplace installation embeds credentials or bypasses persistent setup."""
    claude_plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    claude_mcp = json.loads((ROOT / ".mcp.json").read_text())
    codex_plugin = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text())
    codex_mcp = json.loads((ROOT / ".codex-mcp.json").read_text())
    cursor_plugin = json.loads((ROOT / ".cursor-plugin" / "plugin.json").read_text())
    cursor_mcp = json.loads((ROOT / ".cursor-mcp.json").read_text())

    assert claude_plugin["name"] == codex_plugin["name"] == cursor_plugin["name"] == "ram0"
    assert codex_plugin["mcpServers"] == "./.codex-mcp.json"
    assert codex_plugin["hooks"] == "./hooks/codex-hooks.json"
    assert codex_plugin["skills"] == "./skills/"
    assert cursor_plugin["mcpServers"] == "./.cursor-mcp.json"
    for field in ("hooks", "mcpServers"):
        assert (ROOT / cursor_plugin[field]).is_file()

    definitions = [claude_mcp["mcpServers"]["ram0"], cursor_mcp["mcpServers"]["ram0"]]
    for definition in definitions:
        assert definition["command"] == "python3"
        assert definition["args"][0].endswith("/scripts/mcp_stdio_adapter.py")
        encoded = json.dumps(definition)
        assert "RAM0_API_URL" not in encoded
        assert "RAM0_API_KEY" not in encoded
        assert "Authorization" not in encoded
        assert '"url"' not in encoded

    assert codex_mcp["ram0"] == {
        "command": "./scripts/mcp_stdio_adapter.py",
        "args": [],
        "cwd": ".",
    }


def _run_cursor(name: str, payload: dict, *, ram0_server, tmp_path) -> dict:
    environment = {
        **os.environ,
        "RAM0_API_URL": ram0_server.url,
        "RAM0_API_KEY": "ram0-test-key",
        "RAM0_PLUGIN_DATA": str(tmp_path),
    }
    result = subprocess.run(
        [str(ROOT / "scripts" / name)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=True,
        env=environment,
    )
    return json.loads(result.stdout)


def _git_repo(path: Path, remote: str) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "remote", "add", "origin", remote], check=True)
    return path


def _run_hook(
    name: str,
    payload: dict,
    *,
    ram0_server,
    state_dir: Path,
    extra_environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = {
        **os.environ,
        "RAM0_API_URL": ram0_server.url,
        "RAM0_API_KEY": "ram0-test-key",
        "RAM0_PLUGIN_DATA": str(state_dir),
        **(extra_environment or {}),
    }
    return subprocess.run(
        [str(ROOT / "scripts" / name)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=True,
        env=environment,
    )


def test_automatic_retrieval_uses_current_project_plus_global_without_host_details(ram0_server, tmp_path):
    """Breaks if a hook omits current Git scope, widens recall, or exposes raw host context."""
    repository = _git_repo(tmp_path / "checkout", "git@github.com:olhapi/ram0.git")

    result = _run_hook(
        "on_session_start.sh",
        {"source": "startup", "cwd": str(repository)},
        ram0_server=ram0_server,
        state_dir=tmp_path / "state",
    )

    request = json.loads(ram0_server.requests[-1]["body"])
    assert request["filters"] == {"OR": [{"app_id": "github.com-olhapi-ram0"}, {"app_id": None}]}
    assert "user_id" not in request["filters"]
    assert "github.com-olhapi-ram0" in result.stdout
    assert str(repository) not in result.stdout
    assert "git@github.com:olhapi/ram0.git" not in result.stdout


def test_automatic_capture_writes_current_project_without_scope_metadata(ram0_server, tmp_path):
    """Breaks if automatic capture becomes global or stores project/host context as metadata."""
    repository = _git_repo(tmp_path / "checkout", "git@github.com:olhapi/ram0.git")

    _run_hook(
        "on_stop.sh",
        {"cwd": str(repository), "last_assistant_message": "Decision: The adapter stays narrow."},
        ram0_server=ram0_server,
        state_dir=tmp_path / "state",
    )

    request = json.loads(ram0_server.requests[-1]["body"])
    assert request["app_id"] == "github.com-olhapi-ram0"
    assert not ({"user_id", "app_id", "cwd", "remote", "branch", "api_key"} & set(request["metadata"]))


def test_project_context_remains_available_when_automatic_retrieval_is_disabled(ram0_server, tmp_path):
    """Breaks if disabling auto-recall also removes the project app ID required by manual skills."""
    repository = _git_repo(tmp_path / "checkout", "git@github.com:olhapi/ram0.git")

    result = _run_hook(
        "on_session_start.sh",
        {"source": "startup", "cwd": str(repository)},
        ram0_server=ram0_server,
        state_dir=tmp_path / "state",
        extra_environment={"RAM0_MEMORY_RETRIEVAL": "false"},
    )

    assert "Current Ram0 app_id: github.com-olhapi-ram0" in result.stdout
    assert ram0_server.requests == []


def test_each_hook_invocation_resolves_its_event_cwd(ram0_server, tmp_path):
    """Breaks if a long-lived session reuses stale project context after the host cwd changes."""
    first = _git_repo(tmp_path / "first", "git@github.com:olhapi/first.git")
    second = _git_repo(tmp_path / "second", "git@github.com:olhapi/second.git")
    state_dir = tmp_path / "state"

    for repository in (first, second):
        _run_hook(
            "on_user_prompt.sh",
            {"cwd": str(repository), "prompt": "architecture"},
            ram0_server=ram0_server,
            state_dir=state_dir,
        )

    payloads = [json.loads(request["body"]) for request in ram0_server.requests]
    assert [payload["filters"]["OR"][0]["app_id"] for payload in payloads] == [
        "github.com-olhapi-first",
        "github.com-olhapi-second",
    ]


def test_cursor_prompt_and_session_wrappers_emit_cursor_json(ram0_server, tmp_path):
    """Breaks if shared plain/XML output is returned directly to Cursor."""
    ram0_server.response = {"results": [_trusted_memory("Decision: The hooks remain host-specific.")]}

    prompt = _run_cursor(
        "on_user_prompt_cursor.sh",
        {"prompt": "debug postgres authentication"},
        ram0_server=ram0_server,
        tmp_path=tmp_path,
    )
    session = _run_cursor(
        "on_session_start_cursor.sh", {"source": "startup"}, ram0_server=ram0_server, tmp_path=tmp_path
    )

    assert prompt["continue"] is True
    assert "user_message" in prompt and "ram0-memory-context" in prompt["user_message"]
    assert "additional_context" in session and "ram0-memory-context" in session["additional_context"]


def test_cursor_bash_precompact_and_stop_wrappers_use_real_payloads(ram0_server, tmp_path):
    """Breaks if Cursor tool/timeline payloads are dropped or non-JSON is emitted."""
    ram0_server.response = {"results": [_trusted_memory("Troubleshooting: The prior timeout was resolved by retry.")]}
    bash = _run_cursor(
        "on_bash_output_cursor.sh",
        {"tool_name": "Bash", "tool_input": {"command": "pytest"}, "tool_response": "Error: timeout\n" * 4},
        ram0_server=ram0_server,
        tmp_path=tmp_path,
    )
    transcript = tmp_path / "cursor.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "Follow-up: The category tests remain pending."}]},
            }
        )
    )
    precompact = _run_cursor(
        "on_pre_compact_cursor.sh", {"transcript_path": str(transcript)}, ram0_server=ram0_server, tmp_path=tmp_path
    )
    stop = _run_cursor(
        "on_stop_cursor.sh", {"transcript_path": str(transcript)}, ram0_server=ram0_server, tmp_path=tmp_path
    )

    assert bash["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "additionalContext" in bash["hookSpecificOutput"]
    assert precompact == {}
    assert stop == {}


def test_codex_bundle_uses_plugin_root_and_does_not_require_duplicate_user_hooks():
    """Breaks if bundled Codex hooks point at an unsupported variable or installation requires a second hook copy."""
    hooks = json.loads((ROOT / "hooks" / "codex-hooks.json").read_text())
    commands = list(_commands(hooks))

    assert commands
    assert all("${PLUGIN_ROOT}/scripts/" in command for command in commands)
    assert all("PLUGIN_SCRIPTS" not in command for command in commands)
    assert not (ROOT / "scripts" / "install_codex_hooks.py").exists()


def test_real_codex_install_lists_bundled_ram0_mcp_from_isolated_home(tmp_path):
    """Breaks if the checked-in marketplace/plugin/MCP contract is rejected by the real Codex CLI."""
    codex = shutil.which("codex")
    if codex is None:
        pytest.skip("Codex CLI is not installed")
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    environment = {
        **os.environ,
        "CODEX_HOME": str(codex_home),
    }
    repository = ROOT.parents[1]
    subprocess.run(
        [codex, "plugin", "marketplace", "add", str(repository)],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    installed = subprocess.run(
        [codex, "plugin", "add", "ram0@ram0-plugins", "--json"],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    installed_path = Path(json.loads(installed.stdout)["installedPath"])
    listing = subprocess.run(
        [codex, "mcp", "list", "--json"],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )

    servers = {item["name"]: item for item in json.loads(listing.stdout)}
    transport = servers["ram0"]["transport"]
    assert transport["type"] == "stdio"
    assert transport["command"] == "./scripts/mcp_stdio_adapter.py"
    assert transport["args"] == []
    assert Path(transport["cwd"]).resolve() == installed_path.resolve()
    assert transport["env"] is None
    assert "${PLUGIN_ROOT}/scripts/" in (installed_path / "hooks" / "codex-hooks.json").read_text()
    installed_skills = {
        path.parent.name for path in (installed_path / "skills").glob("*/SKILL.md")
    }
    assert installed_skills == {"ram0-memory", *EXPECTED_WORKFLOW_SKILLS}
    assert "RAM0_API_KEY" not in (codex_home / "config.toml").read_text()


def test_session_start_bootstraps_cli_before_memory_retrieval():
    source = (ROOT / "scripts" / "on_session_start.sh").read_text()
    assert source.index("bootstrap_cli.py") < source.index("memory_capture.py")
    assert "|| true" in source
