"""Stable, client-actionable result contracts for Ram0 MCP tools."""

# Modified for Ram0; see NOTICE and repository history.

import json
from typing import Any

from fastmcp.exceptions import ToolError


_ERRORS = {
    "authentication_required": {
        "message": "Authentication required.",
        "how_to_fix": "Reconnect with a valid Bearer API key.",
        "example": {"authorization": "Bearer <api-key>"},
    },
    "invalid_memory_id": {
        "message": "memory_id must be a valid UUID.",
        "how_to_fix": "Call get_memory with a UUID returned by search_memories or list_memories.",
        "example": {"memory_id": "00000000-0000-0000-0000-000000000000"},
    },
    "memory_not_found": {
        "message": "Memory not found.",
        "how_to_fix": "Call search_memories or list_memories to find a memory ID you can access.",
        "example": {"query": "find the memory you need", "limit": 10},
    },
    "invalid_argument": {
        "message": "content must be a non-empty string and metadata must be an object.",
        "how_to_fix": "Provide a non-empty content string and object metadata without identity, expiration, or category fields.",
        "example": {"content": "I prefer dark mode", "metadata": {"source": "assistant"}},
    },
    "memory_write_unavailable": {
        "message": "Memory write unavailable because the provider quota is exhausted.",
        "how_to_fix": "Resolve your provider quota, then retry this operation once.",
        "example": {"content": "Retry this memory after provider quota is available."},
    },
    "upstream_unavailable": {
        "message": "Memory service is temporarily unavailable.",
        "how_to_fix": "Retry this operation once after five seconds.",
        "example": {"content": "Retry this memory after five seconds."},
    },
}

_INVALID_ARGUMENT_VARIANTS = {
    "search_query": {
        "message": "query must be a non-empty string.",
        "how_to_fix": "Provide a non-empty query string and an integer limit from 1 through 100.",
        "example": {"query": "find my preferences", "limit": 10},
    },
    "search_limit": {
        "message": "limit must be an integer from 1 through 100.",
        "how_to_fix": "Provide an integer limit from 1 through 100.",
        "example": {"query": "find my preferences", "limit": 10},
    },
    "list_limit": {
        "message": "limit must be an integer from 1 through 100.",
        "how_to_fix": "Provide an integer limit from 1 through 100.",
        "example": {"limit": 20},
    },
    "scope": {
        "message": 'scope must be omitted, "project", or "global".',
        "how_to_fix": 'Omit scope for the current project plus global memories, or use "project" or "global".',
        "example": {"scope": "project", "app_id": "github.com-org-repository"},
    },
    "app_id": {
        "message": "app_id is required for default and project scope, and must be omitted for global scope.",
        "how_to_fix": "Provide a normalized project app_id, or use global scope without app_id.",
        "example": {"scope": "project", "app_id": "github.com-org-repository"},
    },
}


def tool_success(**data: Any) -> dict[str, Any]:
    """Return the common successful MCP tool envelope."""
    return {"ok": True, **data}


def tool_error(code: str, *, request_id: str | None = None, variant: str | None = None) -> None:
    """Raise a stable JSON ToolError without server or ownership details."""
    if code == "internal_error":
        if request_id is None:
            raise ValueError("internal_error requires a request ID")
        payload = {
            "ok": False,
            "code": code,
            "request_id": request_id,
        }
        raise ToolError(json.dumps(payload, sort_keys=True, separators=(",", ":")))

    detail = _INVALID_ARGUMENT_VARIANTS[variant] if variant is not None else _ERRORS[code]
    payload = {"ok": False, "code": code, **detail}
    raise ToolError(json.dumps(payload, sort_keys=True, separators=(",", ":")))
