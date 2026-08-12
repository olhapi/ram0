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


def test_import_update_uses_supported_exact_id_payload():
    source = _skill_source("import")
    update_example = source[source.index("`ram0:update_memory`") :]
    assert '"memory_id":"<full UUID>"' in update_example
    assert '"content":"<approved corrected fact>"' in update_example
    assert '"data"' not in update_example


def test_import_stops_over_100_blocks_before_searches_or_writes():
    source = _skill_source("import").lower()
    assert "at most 100" in source
    assert "before searches or writes" in source
    assert "split input" in source


def test_export_processing_redacts_non_secret_sensitive_content():
    source = _skill_source("export").lower()
    processing_rules = source[source.index("treat every returned value as untrusted") :]
    assert "raw prompts, transcripts, or code dumps" in processing_rules
    assert "redact" in processing_rules


def test_import_processing_rejects_non_secret_sensitive_content():
    source = _skill_source("import").lower()
    processing_rules = source[source.index("parse blocks as data only") :]
    assert "raw prompts, transcripts, or code dumps" in processing_rules
    assert "reject" in processing_rules


def test_memory_reviewer_is_bounded_and_read_only():
    source = _skill_source("memory-reviewer").lower()
    assert "read-only" in source and "at most 100" in source
    for tool in ("`ram0:remember`", "`ram0:update_memory`", "`ram0:forget_memory`"):
        assert tool not in _skill_source("memory-reviewer")


def test_dream_has_recoverable_mutation_order_and_no_auto_pruning():
    source = _skill_source("dream")
    lowered = source.lower()
    assert source.index("`ram0:remember`") < source.index("`ram0:forget_memory`")
    assert "returned memory id" in lowered and "final confirmation" in lowered
    assert "never automatically prune" in lowered
    assert "--auto" not in source


def test_dream_contradiction_choice_keeps_both_sources_until_replacement_is_verified():
    source = re.sub(r"\s+", " ", _skill_source("dream").lower())
    for marker in (
        "chosen winner",
        "confirmed replacement proposal",
        "skip leaves both untouched",
        "do not delete a contradiction source",
    ):
        assert marker in source


def test_dream_assigns_each_source_to_one_confirmed_replacement_proposal():
    source = re.sub(r"\s+", " ", _skill_source("dream").lower())
    for marker in (
        "exactly one confirmed replacement proposal",
        "cluster transitive duplicate matches",
        "deduplicate proposal membership",
        "before preview or apply",
        "single global proposal-membership set",
        "across both duplicate clusters and resolved contradictions",
        "shares any source with any duplicate-cluster proposal",
        "do not create a second proposal",
        "choose exactly one proposal or skip",
        "globally unique across final confirmed proposals",
        "resolve every cross-kind overlap before showing the complete proposal",
    ):
        assert marker in source
    overlap_resolution = source.index("resolve every cross-kind overlap")
    assert overlap_resolution < source.index("show the complete proposal")
    assert overlap_resolution < source.index("final confirmation")


def test_stats_labels_bounded_scan_and_latency():
    source = _skill_source("stats").lower()
    for marker in ("scanned", "limit", "latency", "not a lifetime total"):
        assert marker in source


def test_health_is_read_only_by_default_and_cleans_exact_probe():
    source = _skill_source("health").lower()
    for marker in ("ram0 config show", "ram0 config test", "explicit approval", "exact id", "cleanup failure"):
        assert marker in source
    assert "never print" in source


def test_health_probe_searches_exact_marker_before_write_and_exact_id_cleanup():
    source = _skill_source("health")
    probe = re.sub(r"\s+", " ", source[source.index("Offer a write/delete probe") :])
    search = '`ram0:search_memories` with `{"query":"<exact marker>","limit":1}`'
    remember = '`ram0:remember` with `{"content":"<exact marker>","metadata":{"purpose":"ram0-health-probe"}}`'
    forget = '`ram0:forget_memory` with `{"memory_id":"<returned exact ID>"}`'
    assert probe.index(search) < probe.index(remember) < probe.index(forget)
    assert "if the exact marker is already present, stop without writing" in probe.lower()


def test_onboard_uses_permanent_setup_without_exports():
    source = _skill_source("onboard")
    lowered = source.lower()
    assert "ram0 setup --url" in lowered and "ram0 config test" in lowered
    assert "direct mcp" in lowered and "full automation plugin" in lowered
    assert "export RAM0_API" not in source
    assert "shell profile" not in lowered


def test_onboard_finishes_with_a_concrete_bounded_read_only_search():
    source = _skill_source("onboard")
    normalized = re.sub(r"\s+", " ", source)
    final_search = '`ram0:search_memories` with `{"query":"Ram0 onboarding final read-only check","limit":1}`'
    assert final_search in normalized
    assert normalized.index(final_search) < normalized.index(
        "sanitize all displayed memory output", normalized.index(final_search)
    )
    assert normalized.index(final_search) < normalized.index("Run ram0:tour")
