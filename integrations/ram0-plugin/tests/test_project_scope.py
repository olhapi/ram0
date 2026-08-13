# SPDX-FileCopyrightText: 2026 Ram0 contributors
# SPDX-License-Identifier: Apache-2.0
"""Git project identity resolution contracts for the Ram0 plugin."""

from __future__ import annotations

import fcntl
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from project_scope import ProjectScopeError, resolve_project_context


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def git_repo(path: Path, remote: str | None = None) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.name", "Ram0 Tests")
    _git(path, "config", "user.email", "ram0-tests@example.invalid")
    _git(path, "config", "commit.gpgsign", "false")
    (path / "tracked.txt").write_text("project\n", encoding="utf-8")
    _git(path, "add", "tracked.txt")
    _git(path, "commit", "-qm", "test fixture")
    if remote is not None:
        _git(path, "remote", "add", "origin", remote)
    return path


def test_https_and_ssh_origin_resolve_to_same_host_qualified_id(tmp_path, monkeypatch):
    """Breaks if transport syntax changes the project identity."""
    monkeypatch.delenv("RAM0_PROJECT_ID", raising=False)
    https = git_repo(tmp_path / "https", "https://github.com/olhapi/ram0.git")
    ssh = git_repo(tmp_path / "ssh", "git@github.com:olhapi/ram0.git")

    assert resolve_project_context(https, state_dir=tmp_path / "state-https").app_id == "github.com-olhapi-ram0"
    assert resolve_project_context(ssh, state_dir=tmp_path / "state-ssh").app_id == "github.com-olhapi-ram0"


def test_other_host_does_not_collide(tmp_path, monkeypatch):
    """Breaks if host qualification is dropped from a remote-derived ID."""
    monkeypatch.delenv("RAM0_PROJECT_ID", raising=False)
    repo = git_repo(tmp_path / "repo", "git@git.home.olhapi.com:olhapi/ram0.git")

    assert resolve_project_context(repo, state_dir=tmp_path / "state").app_id == "git.home.olhapi.com-olhapi-ram0"


def test_remote_normalization_strips_credentials_port_query_fragment_and_only_lowercases_host(tmp_path, monkeypatch):
    """Breaks if a secret or transport decoration enters the app ID, or path case is lost."""
    monkeypatch.delenv("RAM0_PROJECT_ID", raising=False)
    repo = git_repo(
        tmp_path / "repo",
        "ssh://deploy:private-token@GitHub.COM:2222/Owner/Repo.Name.git?credential=secret#fragment",
    )

    context = resolve_project_context(repo, state_dir=tmp_path / "state")

    assert context.app_id == "github.com-Owner-Repo.Name"
    assert "private-token" not in context.app_id
    assert "2222" not in context.app_id


def test_scp_remote_normalization_strips_query_and_fragment(tmp_path, monkeypatch):
    """Breaks if SCP-like remotes leak transport parameters into project IDs."""
    monkeypatch.delenv("RAM0_PROJECT_ID", raising=False)
    repo = git_repo(
        tmp_path / "repo",
        "git@GitHub.COM:Owner/Repo.git?credential=secret#fragment",
    )

    context = resolve_project_context(repo, state_dir=tmp_path / "state")

    assert context.app_id == "github.com-Owner-Repo"
    assert "secret" not in context.app_id


def test_explicit_project_id_has_priority_and_is_saved_as_opaque_private_aliases(tmp_path):
    """Breaks if RAM0_PROJECT_ID loses priority or project state exposes local repository details."""
    remote = "git@private.example:SecretOwner/SecretRepo.git"
    repo = git_repo(tmp_path / "sensitive-local-name", remote)
    state_dir = tmp_path / "plugin-state"

    context = resolve_project_context(
        repo,
        environment={"RAM0_PROJECT_ID": "explicit-project", "RAM0_PLUGIN_DATA": str(state_dir)},
    )

    mapping_path = state_dir / "project_map.json"
    raw = mapping_path.read_text(encoding="utf-8")
    assert context.app_id == "explicit-project"
    assert stat.S_IMODE(state_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(mapping_path.stat().st_mode) == 0o600
    assert str(repo) not in raw
    assert remote not in raw
    assert "SecretOwner" not in raw
    assert set(json.loads(raw).values()) == {"explicit-project"}
    assert [path for path in state_dir.glob(".project_map.json.*") if path.name != ".project_map.json.lock"] == []


def test_saved_mapping_beats_remote_derivation_and_survives_a_moved_clone(tmp_path, monkeypatch):
    """Breaks if a chosen alias is lost after a clone moves or is recreated elsewhere."""
    remote = "https://github.com/olhapi/ram0.git"
    original = git_repo(tmp_path / "original", remote)
    moved = git_repo(tmp_path / "moved", remote)
    state_dir = tmp_path / "state"
    monkeypatch.setenv("RAM0_PROJECT_ID", "chosen-alias")
    resolve_project_context(original, state_dir=state_dir)
    monkeypatch.delenv("RAM0_PROJECT_ID")

    context = resolve_project_context(moved, state_dir=state_dir)

    assert context.app_id == "chosen-alias"


def test_git_worktrees_share_project_identity_but_report_their_own_root_and_branch(tmp_path, monkeypatch):
    """Breaks if linked worktree folder names split a repository into separate projects."""
    monkeypatch.delenv("RAM0_PROJECT_ID", raising=False)
    repo = git_repo(tmp_path / "primary")
    linked = tmp_path / "different-folder"
    _git(repo, "worktree", "add", "-qb", "topic/worktree", str(linked))
    state_dir = tmp_path / "state"

    primary = resolve_project_context(repo, state_dir=state_dir)
    worktree = resolve_project_context(linked, state_dir=state_dir)

    assert primary.app_id == worktree.app_id == "primary"
    assert primary.root == repo.resolve()
    assert worktree.root == linked.resolve()
    assert worktree.branch == "topic/worktree"


def test_repository_root_name_is_used_from_a_nested_directory_without_an_origin(tmp_path, monkeypatch):
    """Breaks if an arbitrary nested cwd replaces the Git repository fallback identity."""
    monkeypatch.delenv("RAM0_PROJECT_ID", raising=False)
    repo = git_repo(tmp_path / "My Project")
    nested = repo / "src" / "feature"
    nested.mkdir(parents=True)

    context = resolve_project_context(nested, state_dir=tmp_path / "state")

    assert context.app_id == "My-Project"
    assert context.root == repo.resolve()


def test_non_git_cwd_name_is_the_final_fallback(tmp_path, monkeypatch):
    """Breaks if non-Git hosts cannot obtain a bounded local project identity."""
    monkeypatch.delenv("RAM0_PROJECT_ID", raising=False)
    cwd = tmp_path / "Local Project"
    cwd.mkdir()

    context = resolve_project_context(cwd, state_dir=tmp_path / "state")

    assert context.app_id == "Local-Project"
    assert context.root == cwd.resolve()
    assert context.branch == "unknown"


def test_ids_are_deterministically_shortened_to_the_server_limit(tmp_path, monkeypatch):
    """Breaks if a long project identity is unstable or exceeds the REST app_id contract."""
    monkeypatch.delenv("RAM0_PROJECT_ID", raising=False)
    owner = "Owner" + ("A" * 90)
    repository = "Repo" + ("B" * 90)
    first = git_repo(tmp_path / "first", f"https://github.com/{owner}/{repository}.git")
    second = git_repo(tmp_path / "second", f"git@github.com:{owner}/{repository}.git")

    first_id = resolve_project_context(first, state_dir=tmp_path / "state-first").app_id
    second_id = resolve_project_context(second, state_dir=tmp_path / "state-second").app_id

    assert first_id == second_id
    assert len(first_id) == 128
    prefix, separator, suffix = first_id.rpartition("-")
    assert separator == "-"
    assert len(prefix) == 111
    assert len(suffix) == 16
    assert all(character in "0123456789abcdef" for character in suffix)


def test_empty_context_fails_without_disclosing_the_local_path(tmp_path, monkeypatch):
    """Breaks if an empty fallback produces an invalid app ID or leaks cwd in its error."""
    monkeypatch.delenv("RAM0_PROJECT_ID", raising=False)

    with pytest.raises(ProjectScopeError) as raised:
        resolve_project_context(os.path.abspath(os.sep), state_dir=tmp_path / "state")

    assert os.path.abspath(os.sep) not in str(raised.value)
    assert "project context" in str(raised.value).lower()


def test_insecure_or_symlinked_mapping_file_is_rejected_without_following_it(tmp_path, monkeypatch):
    """Breaks if mapping state can be read by another user or redirected to an attacker file."""
    monkeypatch.setenv("RAM0_PROJECT_ID", "safe-project")
    repo = git_repo(tmp_path / "repo")
    state_dir = tmp_path / "state"
    state_dir.mkdir(mode=0o700)
    mapping_path = state_dir / "project_map.json"
    mapping_path.write_text('{}\n', encoding="utf-8")
    mapping_path.chmod(0o644)

    with pytest.raises(ProjectScopeError, match="permissions"):
        resolve_project_context(repo, state_dir=state_dir)

    mapping_path.unlink()
    target = tmp_path / "target.json"
    target.write_text('{}\n', encoding="utf-8")
    mapping_path.symlink_to(target)
    with pytest.raises(ProjectScopeError, match="regular file"):
        resolve_project_context(repo, state_dir=state_dir)
    assert target.read_text(encoding="utf-8") == '{}\n'


def test_concurrent_mapping_writers_wait_re_read_and_merge_without_exposing_context(tmp_path):
    """Breaks if concurrent resolver processes overwrite another repository alias with stale state."""
    first = git_repo(tmp_path / "sensitive-first", "git@private.example:Owner/First.git")
    second = git_repo(tmp_path / "sensitive-second", "git@private.example:Owner/Second.git")
    state_dir = tmp_path / "state"
    state_dir.mkdir(mode=0o700)
    lock_path = state_dir / ".project_map.json.lock"
    lock_path.touch(mode=0o600)
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    command = [
        sys.executable,
        "-c",
        (
            "import sys; from pathlib import Path; "
            "from project_scope import resolve_project_context; "
            "Path(sys.argv[3]).touch(); "
            "resolve_project_context(Path(sys.argv[1]), state_dir=Path(sys.argv[2])); "
            "Path(sys.argv[4]).touch()"
        ),
    ]
    environment = {**os.environ, "PYTHONPATH": str(scripts_dir)}
    first_ready, second_ready = tmp_path / "first-ready", tmp_path / "second-ready"
    first_done, second_done = tmp_path / "first-done", tmp_path / "second-done"

    with lock_path.open("r+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        first_process = subprocess.Popen(
            [*command, str(first), str(state_dir), str(first_ready), str(first_done)],
            env={**environment, "RAM0_PROJECT_ID": "first-project"},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        second_process = subprocess.Popen(
            [*command, str(second), str(state_dir), str(second_ready), str(second_done)],
            env={**environment, "RAM0_PROJECT_ID": "second-project"},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + 5
        while not (first_ready.exists() and second_ready.exists()) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert first_ready.exists()
        assert second_ready.exists()
        time.sleep(1)
        assert first_process.poll() is None
        assert second_process.poll() is None
        assert not first_done.exists()
        assert not second_done.exists()
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    first_output = first_process.communicate(timeout=10)
    second_output = second_process.communicate(timeout=10)
    assert (first_process.returncode, first_output) == (0, ("", ""))
    assert (second_process.returncode, second_output) == (0, ("", ""))

    mapping_path = state_dir / "project_map.json"
    raw = mapping_path.read_text(encoding="utf-8")
    assert {"first-project", "second-project"} <= set(json.loads(raw).values())
    assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600
    assert lock_path.read_text(encoding="utf-8") == ""
    assert str(first) not in raw
    assert str(second) not in raw
    assert "private.example" not in raw


def test_mapping_write_retries_short_os_write(tmp_path, monkeypatch):
    """Breaks if an interrupted short write leaves a truncated project mapping."""
    repo = git_repo(tmp_path / "repo", "git@github.com:olhapi/ram0.git")
    real_write = os.write

    def short_write(descriptor, payload):
        return real_write(descriptor, payload[: max(1, len(payload) // 2)])

    monkeypatch.setattr(os, "write", short_write)
    context = resolve_project_context(repo, state_dir=tmp_path / "state")
    assert context.app_id == "github.com-olhapi-ram0"
    assert context.app_id in json.loads((tmp_path / "state" / "project_map.json").read_text()).values()
