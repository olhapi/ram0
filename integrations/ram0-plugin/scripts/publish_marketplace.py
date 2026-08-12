#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Ram0 contributors
# SPDX-License-Identifier: Apache-2.0
"""Validate, stage, and optionally publish the generated Ram0 marketplace."""

from __future__ import annotations

import argparse
import filecmp
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from export_marketplace import export_marketplace


EXPECTED_REMOTES = {
    "git@github.com:olhapi/ram0-plugins.git",
    "https://github.com/olhapi/ram0-plugins.git",
}


class PublishError(ValueError):
    """The distribution checkout is unsafe to publish."""


def _git(destination: Path, *arguments: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(destination), *arguments],
        check=check,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _same_tree(left: Path, right: Path) -> bool:
    comparison = filecmp.dircmp(left, right, ignore=[".git"])
    if comparison.left_only or comparison.right_only or comparison.funny_files:
        return False
    if any(not filecmp.cmp(left / name, right / name, shallow=False) for name in comparison.common_files):
        return False
    return all(_same_tree(left / name, right / name) for name in comparison.common_dirs)


def _validate_destination(destination: Path, expected_remote: str, expected_branch: str) -> None:
    if not (destination / ".git").exists():
        raise PublishError("Destination must be a Git checkout.")
    if _git(destination, "status", "--porcelain=v1"):
        raise PublishError("Distribution destination must be clean.")
    if _git(destination, "branch", "--show-current") != expected_branch:
        raise PublishError(f"Distribution destination must be on branch {expected_branch}.")
    if expected_remote not in EXPECTED_REMOTES and not Path(expected_remote).is_absolute():
        raise PublishError("Expected remote must be an approved Ram0 URL or an absolute test path.")
    if _git(destination, "remote", "get-url", "origin") != expected_remote:
        raise PublishError("Distribution origin remote is not olhapi/ram0-plugins.")
    remote_ref = f"refs/remotes/origin/{expected_branch}"
    _git(destination, "fetch", "origin", expected_branch, check=False)
    if _git(destination, "show-ref", "--verify", remote_ref, check=False):
        if _git(destination, "rev-parse", "HEAD") != _git(destination, "rev-parse", remote_ref):
            raise PublishError("Distribution destination is not up to date with origin.")


def _validate_existing_tree(destination: Path, source_commit: str) -> None:
    manifest_path = destination / "source-manifest.json"
    tracked = set(_git(destination, "ls-files").splitlines())
    if not manifest_path.exists():
        if tracked:
            raise PublishError("Distribution destination contains unexpected files before its first publish.")
        return
    try:
        manifest = json.loads(manifest_path.read_text())
        expected = set(manifest["files"]) | {"source-manifest.json"}
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise PublishError("Existing source manifest is invalid.") from error
    if tracked != expected:
        raise PublishError("Distribution destination contains unexpected files.")
    if manifest.get("source_commit") == source_commit:
        raise PublishError(f"Distribution already records source commit {source_commit}.")


def publish_marketplace(
    source_root: Path,
    destination: Path,
    source_commit: str,
    *,
    publish: bool = False,
    expected_remote: str = "git@github.com:olhapi/ram0-plugins.git",
    expected_branch: str = "main",
) -> dict[str, object]:
    source_root = Path(source_root)
    destination = Path(destination)
    if publish:
        _validate_destination(destination, expected_remote, expected_branch)
        _validate_existing_tree(destination, source_commit)
    with tempfile.TemporaryDirectory(prefix="ram0-marketplace-publish-") as temporary:
        generated = Path(temporary)
        manifest = export_marketplace(source_root, generated, source_commit)
        changed = not _same_tree(generated, destination)
        if not publish or not changed:
            return {"changed": changed, "manifest": manifest}

        _git(destination, "rm", "-r", "--ignore-unmatch", ".")
        for source in generated.iterdir():
            target = destination / source.name
            if source.is_dir():
                shutil.copytree(source, target, copy_function=shutil.copy2)
            else:
                shutil.copy2(source, target)
        _git(destination, "add", ".")
        _git(destination, "commit", "-m", f"chore: publish Ram0 plugin from {source_commit}")
        _git(destination, "push", "origin", f"HEAD:{expected_branch}")
        return {"changed": True, "manifest": manifest}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--expected-remote", choices=sorted(EXPECTED_REMOTES))
    parser.add_argument("--expected-branch", choices=["main"])
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[3]
    if args.publish and (not args.expected_remote or not args.expected_branch):
        parser.error("--publish requires --expected-remote and --expected-branch")
    result = publish_marketplace(
        repository,
        args.destination,
        args.source_commit,
        publish=args.publish,
        expected_remote=args.expected_remote or "git@github.com:olhapi/ram0-plugins.git",
        expected_branch=args.expected_branch or "main",
    )
    print("Marketplace changed." if result["changed"] else "Marketplace already current.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
