"""Source contracts for Ram0 workflow skills."""

# SPDX-FileCopyrightText: 2026 Ram0 contributors
# SPDX-License-Identifier: Apache-2.0

import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_WORKFLOW_SKILLS = frozenset({
    "remember", "forget", "peek", "tour", "health", "export",
    "import", "dream", "memory-reviewer", "stats", "onboard",
})
ALLOWED_TOOLS = frozenset({
    "remember", "search_memories", "list_memories",
    "get_memory", "update_memory", "forget_memory",
})
FORBIDDEN_OWNERSHIP_FIELDS = frozenset({
    "user_id", "app_id", "project_id", "agent_id", "run_id",
})
PRIVACY_PROHIBITIONS = frozenset({
    "credentials", "raw prompts", "transcripts", "code dumps",
})


def _skill_source(name: str) -> str:
    return (ROOT / "skills" / name / "SKILL.md").read_text()


def test_expected_workflow_skills_have_valid_frontmatter_and_license():
    for name in EXPECTED_WORKFLOW_SKILLS:
        source = _skill_source(name)
        assert source.startswith("---\n")
        assert "<!-- SPDX-FileCopyrightText: 2026 Ram0 contributors -->" in source
        assert "<!-- SPDX-License-Identifier: Apache-2.0 -->" in source
        assert f"\nname: {name}\n" in source
        assert re.search(r"\ndescription: .+\n", source)


@pytest.mark.parametrize("name", sorted(EXPECTED_WORKFLOW_SKILLS))
def test_workflow_skill_uses_only_account_scoped_ram0_contract(name):
    source = _skill_source(name)
    normalized_source = source.lower()
    tools = set(re.findall(r"`ram0:([a-z_]+)`", source))
    assert tools <= ALLOWED_TOOLS
    for forbidden in (*FORBIDDEN_OWNERSHIP_FIELDS, "mcp.mem0.ai", "api.mem0.ai"):
        assert forbidden not in source
    assert "untrusted" in normalized_source
    for prohibited_content in PRIVACY_PROHIBITIONS:
        assert re.search(
            rf"\b(?:do not|never|must not)\b[^.\n]*\b{re.escape(prohibited_content)}\b",
            normalized_source,
        )

    # Workflow-specific behavior is enforced by its planned task because
    # search-before-write, confirmation, read limits, and dream ordering do
    # not apply uniformly to every workflow skill.


def test_remember_searches_before_a_single_write():
    source = _skill_source("remember")
    assert source.index("`ram0:search_memories`") < source.index("`ram0:remember`")
    assert "equivalent" in source.lower()
    assert "one concise" in source.lower()


def test_forget_requires_selection_and_confirmation():
    source = _skill_source("forget").lower()
    assert "exact" in source and "confirm" in source
    assert "never delete" in source
    assert "`ram0:forget_memory`" in _skill_source("forget")


def test_browsing_skills_disclose_bounded_results():
    for name in ("peek", "tour"):
        source = _skill_source(name).lower()
        assert "limit" in source and "untrusted" in source
    assert "account-wide" in _skill_source("tour").lower()


@pytest.mark.parametrize("name", ("remember", "forget", "peek", "tour"))
def test_displaying_memory_results_requires_sensitive_content_sanitization(name):
    source = _skill_source(name).lower()
    assert "before any preview or display" in source
    assert "credentials, raw prompts, transcripts, or code dumps" in source
    assert "[redacted sensitive memory content]" in source


def test_export_is_redacted_bounded_and_non_overwriting():
    source = _skill_source("export").lower()
    for marker in ("ram0-export-", "scan limit", "redact", "overwrite", "confirm"):
        assert marker in source
    assert "complete backup" in source


def test_import_previews_final_batch_before_writes():
    source = _skill_source("import")
    lowered = source.lower()
    for classification in ("add", "update", "duplicate", "rejected"):
        assert classification in lowered
    assert lowered.index("final batch") < source.index("`ram0:remember`")
    assert "write nothing" in lowered and "exact id" in lowered
