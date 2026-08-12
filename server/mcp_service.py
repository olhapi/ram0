"""Owner-scoped read and write gateway used by Ram0 MCP tools."""

import logging
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException
from fastmcp.exceptions import ToolError

from category_models import CATEGORY_GENERATION_KEY, CATEGORY_ORIGIN_KEY, promote_category_fields
from category_runtime import get_category_service
from category_service import CategoryUpdateOutcome
from errors import new_request_id, request_id_var
from mcp_contract import tool_error, tool_success
from memory_authorization import MemoryPrincipal, owner_filters, reject_client_owner, require_owned_memory


_QUOTA_ERROR_NAMES = frozenset({"RateLimitError", "QuotaExceededError"})
_TRANSIENT_ERROR_NAMES = frozenset({"APITimeoutError", "APIConnectionError", "ConnectionError"})
_RESERVED_MCP_METADATA_KEYS = frozenset(
    {
        "user_id",
        "expiration_date",
        "categories",
        "category_status",
        CATEGORY_GENERATION_KEY,
        CATEGORY_ORIGIN_KEY,
    }
)
_MAX_MCP_READ_LIMIT = 100


def current_request_id() -> str:
    """Return the transport request ID, creating one for direct gateway callers."""
    request_id = request_id_var.get()
    return request_id if request_id != "-" else new_request_id()


def _exception_chain(error: BaseException):
    """Yield an exception and its cause/context chain without reusing provider text."""
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _write_error(error: BaseException) -> None:
    """Translate only safe provider classes to agent-visible MCP guidance."""
    chain = tuple(_exception_chain(error))
    if any(type(item).__name__ in _QUOTA_ERROR_NAMES or getattr(item, "status_code", None) == 429 for item in chain):
        tool_error("memory_write_unavailable")
    if any(
        isinstance(item, (ConnectionError, TimeoutError)) or type(item).__name__ in _TRANSIENT_ERROR_NAMES
        for item in chain
    ):
        tool_error("upstream_unavailable")
    request_id = current_request_id()
    logging.warning("mcp_memory_operation_failed request_id=%s", request_id)
    tool_error("internal_error", request_id=request_id)


def reject_client_metadata_controls(value: object) -> None:
    """Reject caller metadata that would overwrite Ram0-owned memory state."""
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, Mapping):
            if _RESERVED_MCP_METADATA_KEYS.intersection(current):
                raise HTTPException(status_code=422, detail="Category fields are server-managed.")
            pending.extend(current.values())
        elif isinstance(current, Sequence) and not isinstance(current, (str, bytes)):
            pending.extend(current)


def _add_category_processing_failed(result: object) -> bool:
    """Read the existing add-hook failure marker without changing its response shape."""
    return bool(
        isinstance(result, Mapping)
        and isinstance(result.get("results"), list)
        and any(
            isinstance(memory, Mapping) and memory.get("category_status") == "failed" for memory in result["results"]
        )
    )


class Ram0McpGateway:
    """Expose only authenticated-account memory operations to MCP clients."""

    def __init__(self, principal: MemoryPrincipal, memory: Any, category_service: Any | None = None):
        self._principal = principal
        self._memory = memory
        self._category_service = category_service

    def _categories(self) -> Any:
        return self._category_service if self._category_service is not None else get_category_service()

    def _owned_memory(self, memory_id: str) -> None:
        try:
            UUID(memory_id)
        except (TypeError, ValueError):
            tool_error("invalid_memory_id")

        try:
            require_owned_memory(memory_id, self._principal, self._memory)
        except HTTPException as error:
            if error.status_code == 404:
                tool_error("memory_not_found")
            _write_error(error)
        except Exception as error:
            _write_error(error)

    @staticmethod
    def _valid_write_arguments(content: object, metadata: object) -> bool:
        return isinstance(content, str) and bool(content.strip()) and (metadata is None or isinstance(metadata, dict))

    @staticmethod
    def _valid_read_limit(limit: object) -> bool:
        return type(limit) is int and 1 <= limit <= _MAX_MCP_READ_LIMIT

    def search_memories(self, query: str, limit: int = 10) -> dict[str, Any]:
        """Search memories owned by the authenticated account."""
        if not isinstance(query, str) or not query.strip():
            tool_error("invalid_argument", variant="search_query")
        if not self._valid_read_limit(limit):
            tool_error("invalid_argument", variant="search_limit")
        try:
            memories = self._memory.search(
                query=query,
                filters=owner_filters(self._principal),
                top_k=limit,
            )
        except Exception as error:
            _write_error(error)
        return tool_success(memories=memories)

    def list_memories(self, limit: int = 20) -> dict[str, Any]:
        """List memories owned by the authenticated account."""
        if not self._valid_read_limit(limit):
            tool_error("invalid_argument", variant="list_limit")
        try:
            memories = self._memory.get_all(
                filters=owner_filters(self._principal),
                top_k=limit,
            )
        except Exception as error:
            _write_error(error)
        return tool_success(memories=memories)

    def get_memory(self, memory_id: str) -> dict[str, Any]:
        """Retrieve one memory only when it belongs to the authenticated account."""
        self._owned_memory(memory_id)
        try:
            return tool_success(memory=self._memory.get(memory_id))
        except Exception as error:
            _write_error(error)

    def remember(self, content: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        """Extract and store memory from one user-authored message."""
        if not self._valid_write_arguments(content, metadata):
            tool_error("invalid_argument")
        try:
            reject_client_owner(metadata)
            reject_client_metadata_controls(metadata)
            service = self._categories()
            catalog = service.resolve_catalog(self._principal.owner_id, None)
            origin_token = str(uuid4())
            params = {
                "messages": [{"role": "user", "content": content}],
                "user_id": self._principal.owner_id,
                "metadata": {**(metadata or {}), CATEGORY_ORIGIN_KEY: origin_token},
            }
            with service.owner_fence(self._principal.owner_id):
                result = promote_category_fields(self._memory.add(**params))
                notices: list[str] = []
                try:
                    result = service.after_add(result, catalog, origin_token=origin_token)
                except Exception:
                    logging.warning("mcp_category_after_add_failed error_code=enqueue_failed")
                    notices.append("Memory saved; category processing may be incomplete.")
                else:
                    if _add_category_processing_failed(result):
                        notices.append("Memory saved; category processing may be incomplete.")
            return tool_success(result=result, **({"notices": notices} if notices else {}))
        except ToolError:
            raise
        except HTTPException as error:
            if error.status_code == 422:
                tool_error("invalid_argument")
            _write_error(error)
        except (TypeError, ValueError):
            tool_error("invalid_argument")
        except Exception as error:
            _write_error(error)

    def update_memory(self, memory_id: str, content: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        """Update one owned memory and reconcile its categories."""
        if not self._valid_write_arguments(content, metadata):
            tool_error("invalid_argument")
        self._owned_memory(memory_id)
        try:
            reject_client_owner(metadata)
            reject_client_metadata_controls(metadata)
            params: dict[str, Any] = {"memory_id": memory_id, "data": content}
            if metadata is not None:
                params["metadata"] = metadata
            outcome = self._categories().run_memory_update(
                memory_id,
                lambda: self._memory.update(**params),
                owner_id=self._principal.owner_id,
                supplied_text=content,
                with_category_outcome=True,
            )
            if not isinstance(outcome, CategoryUpdateOutcome):
                raise RuntimeError("Category update outcome unavailable")
            notices = []
            if outcome.category_processing_failed:
                notices.append("Memory updated; category processing may be incomplete.")
            return tool_success(
                memory=promote_category_fields(outcome.response),
                **({"notices": notices} if notices else {}),
            )
        except ToolError:
            raise
        except HTTPException as error:
            if error.status_code == 422:
                tool_error("invalid_argument")
            _write_error(error)
        except (TypeError, ValueError):
            tool_error("invalid_argument")
        except Exception as error:
            _write_error(error)

    def forget_memory(self, memory_id: str) -> dict[str, Any]:
        """Delete one owned memory and best-effort cancel category work."""
        self._owned_memory(memory_id)
        try:
            self._memory.delete(memory_id=memory_id)
            notices: list[str] = []
            try:
                if not self._categories().after_delete(memory_id, self._principal.owner_id):
                    notices.append("Memory deleted; category cleanup may be incomplete.")
            except Exception:
                logging.warning("mcp_category_after_delete_failed error_code=cancel_failed")
                notices.append("Memory deleted; category cleanup may be incomplete.")
            return tool_success(**({"notices": notices} if notices else {}))
        except ToolError:
            raise
        except Exception as error:
            _write_error(error)
