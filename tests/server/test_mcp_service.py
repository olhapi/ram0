"""Owner-scoped MCP gateway contracts."""

import json
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from fastmcp.exceptions import ToolError

from category_service import CategoryUpdateOutcome
from mcp_service import Ram0McpGateway
from memory_authorization import MemoryPrincipal


OWNER_ID = "00000000-0000-0000-0000-000000000001"
FOREIGN_OWNER_ID = "00000000-0000-0000-0000-000000000002"
MEMORY_ID = "00000000-0000-0000-0000-000000000123"


class FakeMemory:
    def __init__(self, row):
        self.row = row
        self.vector_store = SimpleNamespace(get=lambda _memory_id: row)
        self.search_calls = []
        self.list_calls = []
        self.add_calls = []
        self.update_calls = []
        self.delete_calls = []

    def search(self, **kwargs):
        self.search_calls.append(kwargs)
        return []

    def get_all(self, **kwargs):
        self.list_calls.append(kwargs)
        return []

    def get(self, _memory_id):
        return self.row

    def add(self, **kwargs):
        self.add_calls.append(kwargs)
        return {"results": [{"id": MEMORY_ID, "event": "ADD", "memory": kwargs["messages"][0]["content"]}]}

    def update(self, **kwargs):
        self.update_calls.append(kwargs)
        return {"id": kwargs["memory_id"], "memory": kwargs["data"], "metadata": kwargs.get("metadata")}

    def delete(self, **kwargs):
        self.delete_calls.append(kwargs)


class FakeCategoryService:
    def __init__(self):
        self.after_add_calls = []
        self.run_update_calls = []
        self.after_delete_calls = []
        self.resolve_catalog_calls = []

    def resolve_catalog(self, owner_id, request_catalog):
        self.resolve_catalog_calls.append((owner_id, request_catalog))
        return "project-catalog"

    def owner_fence(self, owner_id):
        assert owner_id == OWNER_ID
        return nullcontext()

    def after_add(self, response, catalog, *, origin_token):
        self.after_add_calls.append((response, catalog, origin_token))
        return {**response, "category_status": "pending"}

    def run_memory_update(self, memory_id, operation, *, owner_id, supplied_text, with_category_outcome=False):
        assert owner_id == OWNER_ID
        self.run_update_calls.append((memory_id, supplied_text))
        response = operation()
        if with_category_outcome:
            return CategoryUpdateOutcome(response=response, category_processing_failed=False)
        return response

    def after_delete(self, memory_id, owner_id):
        self.after_delete_calls.append((memory_id, owner_id))
        return False


@pytest.fixture
def principal():
    return MemoryPrincipal(owner_id=OWNER_ID)


def test_search_and_list_use_the_shared_owner_filter_helpers(principal):
    memory = FakeMemory(SimpleNamespace(payload={"user_id": OWNER_ID}))
    gateway = Ram0McpGateway(principal, memory)

    assert gateway.search_memories("test query", 7) == {"ok": True, "memories": []}
    assert gateway.list_memories(4) == {"ok": True, "memories": []}

    assert memory.search_calls == [{"query": "test query", "filters": {"user_id": OWNER_ID}, "top_k": 7}]
    assert memory.list_calls == [{"filters": {"user_id": OWNER_ID}, "top_k": 4}]


@pytest.mark.parametrize(
    "row",
    [
        None,
        SimpleNamespace(payload={"user_id": FOREIGN_OWNER_ID}),
    ],
)
def test_missing_and_foreign_records_return_the_same_guided_error(principal, row):
    memory = FakeMemory(row)
    gateway = Ram0McpGateway(principal, memory)

    with pytest.raises(ToolError) as raised:
        gateway.get_memory(MEMORY_ID)

    error = json.loads(str(raised.value))
    assert error["code"] == "memory_not_found"
    assert error == {
        "code": "memory_not_found",
        "example": {"query": "find the memory you need", "limit": 10},
        "how_to_fix": "Call search_memories or list_memories to find a memory ID you can access.",
        "message": "Memory not found.",
        "ok": False,
    }


def test_malformed_memory_id_has_guided_error_before_vector_lookup(principal):
    memory = FakeMemory(SimpleNamespace(payload={"user_id": OWNER_ID}))
    gateway = Ram0McpGateway(principal, memory)

    with pytest.raises(ToolError) as raised:
        gateway.get_memory("not-a-uuid")

    assert json.loads(str(raised.value))["code"] == "invalid_memory_id"


def test_mcp_remember_resolves_the_authenticated_owner_catalog(principal):
    """Replacing the principal owner with caller input would let a write cross accounts."""
    memory = FakeMemory(SimpleNamespace(payload={"user_id": OWNER_ID}))
    categories = FakeCategoryService()
    gateway = Ram0McpGateway(principal, memory, categories)

    response = gateway.remember("Keep this private", {"source": "mcp"})

    assert response["ok"] is True
    assert response["result"]["category_status"] == "pending"
    assert memory.add_calls[0]["messages"] == [{"role": "user", "content": "Keep this private"}]
    assert memory.add_calls[0]["user_id"] == OWNER_ID
    assert memory.add_calls[0]["metadata"]["source"] == "mcp"
    assert isinstance(memory.add_calls[0]["metadata"]["_category_origin"], str)
    assert categories.resolve_catalog_calls == [(OWNER_ID, None)]
    assert categories.after_add_calls[0][1] == "project-catalog"


@pytest.mark.parametrize("operation", ["remember", "update"])
@pytest.mark.parametrize(
    "metadata",
    [
        {"nested": {"user_id": FOREIGN_OWNER_ID}},
        {"nested": [{"expiration_date": "2099-01-01"}]},
        {"nested": {"categories": ["billing"]}},
        {"nested": {"category_status": "completed"}},
        {"nested": {"_category_generation": "request-controlled"}},
        {"nested": {"_category_origin": "request-controlled"}},
    ],
)
def test_write_metadata_rejects_ram0_managed_fields(principal, operation, metadata):
    """Forwarding Ram0-managed fields lets an MCP caller control protected memory state."""
    memory = FakeMemory(SimpleNamespace(payload={"user_id": OWNER_ID}))
    gateway = Ram0McpGateway(principal, memory, FakeCategoryService())

    with pytest.raises(ToolError) as raised:
        if operation == "remember":
            gateway.remember("private memory", metadata)
        else:
            gateway.update_memory(MEMORY_ID, "private memory", metadata)

    assert json.loads(str(raised.value))["code"] == "invalid_argument"
    assert memory.add_calls == []
    assert memory.update_calls == []


@pytest.mark.parametrize("query", ["", "   "])
def test_search_rejects_blank_queries_with_search_specific_guidance(principal, query):
    """Forwarding an empty query makes provider failures look like internal MCP failures."""
    memory = FakeMemory(SimpleNamespace(payload={"user_id": OWNER_ID}))
    gateway = Ram0McpGateway(principal, memory)

    with pytest.raises(ToolError) as raised:
        gateway.search_memories(query)

    error = json.loads(str(raised.value))
    assert error == {
        "code": "invalid_argument",
        "example": {"limit": 10, "query": "find my preferences"},
        "how_to_fix": "Provide a non-empty query string and an integer limit from 1 through 100.",
        "message": "query must be a non-empty string.",
        "ok": False,
    }
    assert memory.search_calls == []


@pytest.mark.parametrize(
    ("operation", "limit", "example"),
    [
        ("search", 0, {"limit": 10, "query": "find my preferences"}),
        ("search", 101, {"limit": 10, "query": "find my preferences"}),
        ("list", 0, {"limit": 20}),
        ("list", 101, {"limit": 20}),
    ],
)
def test_read_limits_have_guided_range_errors_at_the_mcp_service_boundary(principal, operation, limit, example):
    """Out-of-range limits must not be translated as opaque provider failures."""
    memory = FakeMemory(SimpleNamespace(payload={"user_id": OWNER_ID}))
    gateway = Ram0McpGateway(principal, memory)

    with pytest.raises(ToolError) as raised:
        if operation == "search":
            gateway.search_memories("find preferences", limit)
        else:
            gateway.list_memories(limit)

    error = json.loads(str(raised.value))
    assert error["code"] == "invalid_argument"
    assert error["example"] == example
    assert error["how_to_fix"] == "Provide an integer limit from 1 through 100."
    assert error["message"] == "limit must be an integer from 1 through 100."
    assert memory.search_calls == []
    assert memory.list_calls == []


@pytest.mark.parametrize("operation", ["update", "forget"])
def test_write_operations_hide_foreign_memory_ids(principal, operation):
    """Skipping the shared row check would permit mutations of a guessed foreign UUID."""
    memory = FakeMemory(SimpleNamespace(payload={"user_id": FOREIGN_OWNER_ID}))
    categories = FakeCategoryService()
    gateway = Ram0McpGateway(principal, memory, categories)

    with pytest.raises(ToolError) as raised:
        if operation == "update":
            gateway.update_memory(MEMORY_ID, "new text")
        else:
            gateway.forget_memory(MEMORY_ID)

    assert json.loads(str(raised.value))["code"] == "memory_not_found"
    assert memory.update_calls == []
    assert memory.delete_calls == []


def test_update_and_forget_run_the_existing_category_hooks(principal):
    """Bypassing category hooks leaves stale classification work after write operations."""
    memory = FakeMemory(SimpleNamespace(payload={"user_id": OWNER_ID}))
    categories = FakeCategoryService()
    gateway = Ram0McpGateway(principal, memory, categories)

    updated = gateway.update_memory(MEMORY_ID, "new text", {"source": "mcp"})
    forgotten = gateway.forget_memory(MEMORY_ID)

    assert updated == {
        "ok": True,
        "memory": {
            "id": MEMORY_ID,
            "memory": "new text",
            "metadata": {"source": "mcp"},
            "categories": None,
            "category_status": "unclassified",
        },
    }
    assert categories.run_update_calls == [(MEMORY_ID, "new text")]
    assert memory.update_calls == [{"memory_id": MEMORY_ID, "data": "new text", "metadata": {"source": "mcp"}}]
    assert forgotten == {
        "ok": True,
        "notices": ["Memory deleted; category cleanup may be incomplete."],
    }
    assert categories.after_delete_calls == [(MEMORY_ID, OWNER_ID)]


def test_successful_add_and_update_disclose_swallowed_category_failures(principal):
    """A successful core write without a notice hides failed category work from the MCP client."""
    memory = FakeMemory(SimpleNamespace(payload={"user_id": OWNER_ID}))
    categories = FakeCategoryService()
    categories.after_add = lambda response, _catalog, *, origin_token: {
        "results": [{**response["results"][0], "categories": [], "category_status": "failed"}]
    }
    categories.run_memory_update = lambda _memory_id, operation, *, owner_id, supplied_text, with_category_outcome: (
        CategoryUpdateOutcome(response=operation(), category_processing_failed=True)
    )
    gateway = Ram0McpGateway(principal, memory, categories)

    added = gateway.remember("private memory")
    updated = gateway.update_memory(MEMORY_ID, "updated memory")

    assert added["ok"] is True
    assert added["notices"] == ["Memory saved; category processing may be incomplete."]
    assert updated["ok"] is True
    assert updated["notices"] == ["Memory updated; category processing may be incomplete."]


def test_provider_quota_returns_non_retryable_remediation_guidance(principal):
    """Treating quota as transient would make the client waste its only retry."""
    memory = FakeMemory(SimpleNamespace(payload={"user_id": OWNER_ID}))
    memory.add = lambda **_kwargs: (_ for _ in ()).throw(ProviderQuotaError("provider secret"))
    gateway = Ram0McpGateway(principal, memory, FakeCategoryService())

    with pytest.raises(ToolError) as raised:
        gateway.remember("private memory")

    error = json.loads(str(raised.value))
    assert error["code"] == "memory_write_unavailable"
    assert error["how_to_fix"] == "Resolve your provider quota, then retry this operation once."
    assert "provider secret" not in str(error)


@pytest.mark.parametrize("error", [TimeoutError("provider secret"), ConnectionError("provider secret")])
def test_transient_provider_failures_retry_once_after_five_seconds(principal, error):
    """Mapping connection failures to quota guidance would prevent the permitted delayed retry."""
    memory = FakeMemory(SimpleNamespace(payload={"user_id": OWNER_ID}))
    memory.add = lambda **_kwargs: (_ for _ in ()).throw(error)
    gateway = Ram0McpGateway(principal, memory, FakeCategoryService())

    with pytest.raises(ToolError) as raised:
        gateway.remember("private memory")

    response = json.loads(str(raised.value))
    assert response["code"] == "upstream_unavailable"
    assert response["how_to_fix"] == "Retry this operation once after five seconds."
    assert "provider secret" not in str(response)


def test_unexpected_write_errors_hide_secrets_and_include_the_request_id(principal, monkeypatch):
    """Forwarding an unexpected exception leaks provider credentials to the MCP client."""
    memory = FakeMemory(SimpleNamespace(payload={"user_id": OWNER_ID}))
    memory.add = lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("token=very-secret"))
    monkeypatch.setattr("mcp_service.current_request_id", lambda: "write-req-123")
    gateway = Ram0McpGateway(principal, memory, FakeCategoryService())

    with pytest.raises(ToolError) as raised:
        gateway.remember("private memory")

    error = json.loads(str(raised.value))
    assert error["code"] == "internal_error"
    assert error["request_id"] == "write-req-123"
    assert "very-secret" not in str(error)


class ProviderQuotaError(Exception):
    status_code = 429
