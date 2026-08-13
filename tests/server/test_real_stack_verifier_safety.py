"""Safety and acceptance contracts for the disposable real-stack verifier."""

# Modified for Ram0; see NOTICE and repository history.

from pathlib import Path

import pytest

from scripts import verify_multi_user_real_stack as verifier


def test_verifier_failure_never_touches_repository_history_sentinel(tmp_path, monkeypatch):
    repository_history = tmp_path / "repository" / "server" / "history" / "history.db"
    repository_history.parent.mkdir(parents=True)
    repository_history.write_bytes(b"user-owned-history-sentinel")
    monkeypatch.setattr(verifier, "SERVER", repository_history.parents[1])
    monkeypatch.setattr(verifier, "HISTORY_DB", repository_history, raising=False)

    def stop_before_external_work(*_args, **_kwargs):
        raise RuntimeError("intentional verifier stop")

    monkeypatch.setattr(verifier, "run", stop_before_external_work)

    with pytest.raises(RuntimeError, match="intentional verifier stop"):
        verifier.main()

    assert repository_history.read_bytes() == b"user-owned-history-sentinel"


def test_verifier_source_does_not_unlink_repository_history_database():
    source = Path(verifier.__file__).read_text()

    assert 'SERVER / "history" / "history.db"' not in source
    assert "HISTORY_DB.unlink" not in source


def test_verifier_source_proves_account_local_app_scopes_and_restore_state():
    source = Path(verifier.__file__).read_text()

    for contract in (
        '"global"',
        '"app-a"',
        '"app-b"',
        '"default project plus global"',
        '"project exact"',
        '"global owner-wide"',
        "app entity deletion",
        "payload->>'app_id'",
        "app_scopes=true",
    ):
        assert contract in source

    assert 'client.delete(f"/entities/app/' in source
    assert 'scope_memory_ids[other_role]["app-a"]' in source
    assert "delete_app_memory_ids[role]" in source
    assert "delete_app_memory_ids[other_role]" in source
    assert 'delete_owner_index = 0 if auth_kind == "JWT" else 1' in source
    assert 'from fastmcp import Client as McpClient' in source
    assert 'await mcp.call_tool("remember"' in source
    assert 'await mcp.call_tool("list_memories"' in source
    assert "MCP default scope did not include project plus global" in source
