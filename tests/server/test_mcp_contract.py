"""Stable success and guided-error contracts for Ram0 MCP tools."""

import json

import pytest

pytest.importorskip("fastmcp", reason="fastmcp not installed")

from fastmcp.exceptions import ToolError

from mcp_contract import tool_error, tool_success


def test_success_results_have_a_stable_ok_envelope():
    result = tool_success(memories=[])

    assert result == {"ok": True, "memories": []}


def test_expected_errors_are_stable_guided_tool_errors():
    with pytest.raises(ToolError) as raised:
        tool_error("invalid_memory_id")

    error = json.loads(str(raised.value))

    assert error == {
        "code": "invalid_memory_id",
        "example": {"memory_id": "00000000-0000-0000-0000-000000000000"},
        "how_to_fix": "Call get_memory with a UUID returned by search_memories or list_memories.",
        "message": "memory_id must be a valid UUID.",
        "ok": False,
    }


def test_internal_errors_include_only_a_request_id():
    """Returning a provider exception would disclose credentials or private memory content."""
    with pytest.raises(ToolError) as raised:
        tool_error("internal_error", request_id="write-req-123")

    assert json.loads(str(raised.value)) == {
        "code": "internal_error",
        "ok": False,
        "request_id": "write-req-123",
    }
