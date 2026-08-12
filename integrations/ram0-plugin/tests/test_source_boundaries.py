"""Regression tests for the isolated Ram0 plugin source boundary."""

from __future__ import annotations

import re
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PACKAGE_ROOT.parents[1]
RUNTIME_SUFFIXES = {".json", ".py", ".sh", ".toml", ".ts"}


def _runtime_sources() -> dict[Path, str]:
    return {
        path.relative_to(PACKAGE_ROOT): path.read_text()
        for path in PACKAGE_ROOT.rglob("*")
        if path.is_file()
        and not ({"tests", "node_modules", "dist"} & set(path.parts))
        and not path.name.endswith(".test.ts")
        and path.suffix in RUNTIME_SUFFIXES
    }


def test_runtime_sources_exclude_vendor_endpoints_telemetry_and_mem0_credentials():
    """Breaks if the isolated package gains a hosted vendor target, analytics channel, or Mem0 key."""
    prohibited = ("api.mem0.ai", "mcp.mem0.ai", "posthog", "telemetry", "MEM0_API_KEY", "mem0ai")

    violations = {
        path: token
        for path, source in _runtime_sources().items()
        for token in prohibited
        if token.lower() in source.lower()
    }

    assert violations == {}


def test_runtime_request_sources_do_not_inject_identity_or_expiration_json_fields():
    """Breaks if runtime request construction begins emitting server-owned identity or expiry keys."""
    prohibited_json_key = re.compile(r"""["'](?:user_id|app_id|run_id|expiration_date)["']\s*:""")

    violations = {
        path: match.group(0)
        for path, source in _runtime_sources().items()
        if (match := prohibited_json_key.search(source)) is not None
    }

    assert violations == {}


def test_ram0_memory_skill_is_packaged_and_independently_installable():
    """Breaks if plugin installs or `npx skills add --skill ram0-memory` cannot discover the same skill."""
    bundled = PACKAGE_ROOT / "skills" / "ram0-memory" / "SKILL.md"
    independent = REPOSITORY_ROOT / "skills" / "ram0-memory" / "SKILL.md"

    assert bundled.is_file()
    assert independent.is_file()
    assert bundled.read_bytes() == independent.read_bytes()
