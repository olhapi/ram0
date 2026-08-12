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
    tools = set(re.findall(r"`ram0:([a-z_]+)`", source))
    assert tools <= ALLOWED_TOOLS
    for forbidden in ("user_id", "app_id", "project_id", "mcp.mem0.ai", "api.mem0.ai"):
        assert forbidden not in source
    assert "untrusted" in source.lower()
