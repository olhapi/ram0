# SPDX-FileCopyrightText: 2026 Ram0 contributors
# SPDX-License-Identifier: Apache-2.0
"""Safety contracts for publishing the generated Ram0 marketplace."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from publish_marketplace import PublishError, publish_marketplace


REPOSITORY = Path(__file__).resolve().parents[3]


def _git(path: Path, *arguments: str) -> str:
    return subprocess.run(["git", "-C", str(path), *arguments], check=True, capture_output=True, text=True).stdout.strip()


def _commit(path: Path, message: str) -> None:
    subprocess.run(
        [
            "git", "-C", str(path), "-c", "commit.gpgsign=false",
            "-c", "user.name=Ram0", "-c", "user.email=ram0@example.invalid",
            "commit", "-m", message,
        ],
        check=True,
        capture_output=True,
    )


def _destination(tmp_path: Path, remote: str = "git@github.com:olhapi/ram0-plugins.git") -> Path:
    destination = tmp_path / "destination"
    destination.mkdir(parents=True)
    _git(destination, "init", "-b", "main")
    _git(destination, "remote", "add", "origin", remote)
    (destination / "README.md").write_text("initial\n")
    _git(destination, "add", ".")
    _commit(destination, "initial")
    return destination


def test_dry_run_does_not_mutate_destination(tmp_path):
    destination = _destination(tmp_path)
    before = _git(destination, "status", "--porcelain=v1")
    result = publish_marketplace(REPOSITORY, destination, "d" * 40, publish=False)

    assert result["changed"] is True
    assert _git(destination, "status", "--porcelain=v1") == before
    assert _git(destination, "log", "-1", "--format=%s") == "initial"


def test_publish_refuses_dirty_wrong_remote_or_wrong_branch(tmp_path):
    dirty = _destination(tmp_path / "dirty")
    (dirty / "untracked").write_text("no")
    with pytest.raises(PublishError, match="clean"):
        publish_marketplace(REPOSITORY, dirty, "e" * 40, publish=True)

    wrong_remote = _destination(tmp_path / "remote", "git@github.com:attacker/ram0-plugins.git")
    with pytest.raises(PublishError, match="remote"):
        publish_marketplace(REPOSITORY, wrong_remote, "e" * 40, publish=True)

    wrong_branch = _destination(tmp_path / "branch")
    _git(wrong_branch, "switch", "-c", "other")
    with pytest.raises(PublishError, match="branch"):
        publish_marketplace(REPOSITORY, wrong_branch, "e" * 40, publish=True)


def test_publish_refuses_stale_remote_unexpected_files_and_unchanged_source(tmp_path):
    bare = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", str(bare))
    destination = _destination(tmp_path / "checkout", str(bare))
    _git(destination, "push", "-u", "origin", "main")

    other = tmp_path / "other"
    subprocess.run(["git", "clone", "--branch", "main", str(bare), str(other)], check=True, capture_output=True)
    (other / "remote-change").write_text("new\n")
    _git(other, "add", ".")
    _commit(other, "remote change")
    _git(other, "push", "origin", "main")
    with pytest.raises(PublishError, match="up to date"):
        publish_marketplace(
            REPOSITORY, destination, "a" * 40, publish=True, expected_remote=str(bare)
        )

    _git(destination, "pull", "--ff-only")
    with pytest.raises(PublishError, match="unexpected"):
        publish_marketplace(
            REPOSITORY, destination, "a" * 40, publish=True, expected_remote=str(bare)
        )


def test_publish_commits_only_generated_tree_and_pushes(tmp_path):
    bare = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", str(bare))
    destination = tmp_path / "destination"
    subprocess.run(["git", "clone", str(bare), str(destination)], check=True, capture_output=True)
    _git(destination, "switch", "-c", "main")
    _git(destination, "config", "user.name", "Ram0 publisher")
    _git(destination, "config", "user.email", "ram0@example.invalid")
    _git(destination, "config", "commit.gpgsign", "false")

    source_commit = "b" * 40
    result = publish_marketplace(
        REPOSITORY, destination, source_commit, publish=True, expected_remote=str(bare)
    )

    assert result["changed"] is True
    assert _git(destination, "status", "--porcelain=v1") == ""
    assert _git(destination, "log", "-1", "--format=%s") == f"chore: publish Ram0 plugin from {source_commit}"
    assert _git(destination, "ls-files", "unexpected") == ""
    assert _git(destination, "show", "HEAD:source-manifest.json")

    with pytest.raises(PublishError, match="already records"):
        publish_marketplace(
            REPOSITORY, destination, source_commit, publish=True, expected_remote=str(bare)
        )
