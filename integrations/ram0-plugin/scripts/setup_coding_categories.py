#!/usr/bin/env python3
"""Add the curated coding catalog once without replacing owner edits."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ram0_client import Ram0Client, Ram0ClientError
from ram0_settings import load_settings


CODING_CATEGORIES = [
    {
        "name": "architecture_decisions",
        "description": "System design choices, boundaries, trade-offs, and adopted patterns.",
    },
    {"name": "api_design", "description": "API contracts, endpoint behavior, schemas, compatibility, and versioning."},
    {"name": "data_models", "description": "Schemas, constraints, relationships, migrations, and data-flow decisions."},
    {"name": "algorithms", "description": "Algorithm choices, complexity trade-offs, and implementation constraints."},
    {"name": "dependencies", "description": "Dependency selections, versions, alternatives, and upgrade constraints."},
    {
        "name": "environment_setup",
        "description": "Local tooling, package managers, configuration, and reproducible setup.",
    },
    {
        "name": "testing_strategy",
        "description": "Test approaches, fixtures, verification commands, and regression coverage.",
    },
    {
        "name": "debugging_notes",
        "description": "Root causes, diagnostic evidence, failed approaches, and proven fixes.",
    },
    {
        "name": "performance",
        "description": "Profiles, bottlenecks, measurements, optimizations, and regression boundaries.",
    },
    {
        "name": "security",
        "description": "Authentication, authorization, secrets handling, trust boundaries, and mitigations.",
    },
    {"name": "deployment", "description": "Build, release, deployment, rollback, and operational runbooks."},
    {
        "name": "code_conventions",
        "description": "Naming, formatting, module patterns, error handling, and team conventions.",
    },
    {
        "name": "error_handling",
        "description": "Failure modes, recovery behavior, safe errors, retries, and fail-open or fail-closed decisions.",
    },
    {
        "name": "refactoring_history",
        "description": "Structural changes, motivations, compatibility, and migration notes.",
    },
    {
        "name": "integrations",
        "description": "External system contracts, adapters, hooks, and interoperability constraints.",
    },
    {"name": "onboarding", "description": "Installation, first-run setup, prerequisites, and contributor orientation."},
    {
        "name": "project_meta",
        "description": "Project status, durable follow-ups, ownership boundaries, and next actions.",
    },
]


def _data_directory(value: Path | None = None) -> Path:
    if value is not None:
        return value
    configured = os.environ.get("RAM0_PLUGIN_DATA") or os.environ.get("CLAUDE_PLUGIN_DATA")
    return Path(configured) if configured else Path.home() / ".ram0" / "plugin-data"


def _catalog_definitions(response: Any) -> list[dict[str, str]]:
    if not isinstance(response, dict):
        return []
    values = [response.get("saved", []), response.get("active", [])]
    definitions: list[dict[str, str]] = []
    names: set[str] = set()
    for value in values:
        if not isinstance(value, list):
            continue
        for item in value:
            normalized: dict[str, str] | None = None
            if (
                isinstance(item, dict)
                and isinstance(item.get("name"), str)
                and isinstance(item.get("description"), str)
            ):
                normalized = dict(item)
            elif isinstance(item, dict) and len(item) == 1:
                name, description = next(iter(item.items()))
                if isinstance(name, str) and isinstance(description, str):
                    normalized = {"name": name, "description": description}
            if normalized is not None and normalized["name"] not in names:
                definitions.append(normalized)
                names.add(normalized["name"])
    return definitions


def onboard_categories(client: Any, *, data_dir: Path | None = None, marker_scope: str = "default") -> bool:
    """Create absent names individually so concurrent owner edits are never replaced."""
    directory = _data_directory(data_dir)
    suffix = "" if marker_scope == "default" else f"-{marker_scope}"
    marker = directory / f"coding-categories-onboarded{suffix}"
    if marker.exists():
        return False
    existing = _catalog_definitions(client.get_categories())
    names = {item["name"] for item in existing}
    missing = [dict(item) for item in CODING_CATEGORIES if item["name"] not in names]
    for definition in missing:
        try:
            client.create_category(definition)
        except Ram0ClientError as error:
            if error.status != 400:
                raise
            latest_names = {item["name"] for item in _catalog_definitions(client.get_categories())}
            if definition["name"] not in latest_names:
                raise
    directory.mkdir(parents=True, exist_ok=True)
    marker.write_text("complete\n")
    return bool(missing)


def main(
    *,
    environment: Mapping[str, str] | None = None,
    home: Path | None = None,
    data_dir: Path | None = None,
) -> int:
    try:
        settings = load_settings(environment, home=home)
        if not settings.api_key:
            return 0
        marker_scope = settings.owner_fingerprint[:16]
        onboard_categories(Ram0Client(settings.api_url, settings.api_key), data_dir=data_dir, marker_scope=marker_scope)
    except (Ram0ClientError, ValueError, TypeError, OSError):
        print("Ram0 category onboarding deferred: run `ram0 setup` and `ram0 config test`.", file=os.sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
