"""MCP transport mounting and authentication boundary contracts."""

# ruff: noqa: E402 -- server initialization reads these test-only environment values at import time.

import asyncio
import json
import os
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")

from fastapi import HTTPException
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("AUTH_DISABLED", "true")
os.environ["HISTORY_DB_PATH"] = "/tmp/ram0-mcp-history.db"
os.environ.setdefault("OPENAI_API_KEY", "test-key")

import auth

auth.AUTH_DISABLED = True

import main
import mcp_auth
import mcp_server
from memory_authorization import MemoryPrincipal


async def invoke(app, headers: list[tuple[bytes, bytes]]):
    sent = []
    complete = False

    async def receive():
        nonlocal complete
        if complete:
            return {"type": "http.disconnect"}
        complete = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await app({"type": "http", "method": "POST", "path": "/mcp", "headers": headers}, receive, send)
    return sent


def test_mcp_auth_boundary_rejects_invalid_transport_before_the_inner_application(monkeypatch):
    """Removing the ASGI gate would let header-authenticated traffic reach FastMCP."""
    reached = []

    async def inner(scope, receive, send):
        reached.append(scope)
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def reject(_request):
        raise HTTPException(status_code=401, detail="Authentication required.", headers={"WWW-Authenticate": "Bearer"})

    monkeypatch.setattr(mcp_auth, "require_mcp_bearer", reject)
    messages = asyncio.run(invoke(mcp_auth.MCPBearerAuthMiddleware(inner), []))

    assert reached == []
    assert messages[0]["status"] == 401
    assert dict(messages[0]["headers"])[b"www-authenticate"] == b"Bearer"


def test_mcp_auth_boundary_passes_only_the_immutable_principal_to_the_inner_application(monkeypatch):
    """Storing the mutable account/key object in scope would leak transport credentials downstream."""
    reached = []
    principal = MemoryPrincipal(owner_id="00000000-0000-0000-0000-000000000111")

    async def inner(scope, receive, send):
        reached.append(scope)
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def authenticate(_request):
        return principal

    monkeypatch.setattr(mcp_auth, "require_mcp_bearer", authenticate)
    messages = asyncio.run(
        invoke(mcp_auth.MCPBearerAuthMiddleware(inner), [(b"authorization", b"Bearer valid-account-key")])
    )

    assert messages[0]["status"] == 204
    assert reached[0][mcp_auth.MCP_PRINCIPAL_SCOPE_KEY] is principal
    assert set(reached[0]) & {"mcp_user", "mcp_api_key", "mcp_raw_key"} == set()


def test_fastmcp_streamable_http_is_mounted_once_at_mcp():
    """Changing the inner path to /mcp would silently create a broken /mcp/mcp endpoint."""
    mount = next(route for route in main.app.routes if getattr(route, "app", None) is mcp_server.mcp_authenticated_app)

    assert mount.path == "/mcp"
    assert any(getattr(route, "path", None) == "/" for route in mcp_server.mcp_http_app.routes)
    assert all(getattr(route, "path", None) != "/mcp" for route in mcp_server.mcp_http_app.routes)


def test_mcp_registers_only_scoped_memory_tool_schemas():
    """Additional tool arguments would reintroduce caller-controlled ownership scope."""
    server, _ = mcp_server.create_mcp_http_app()
    tools = asyncio.run(server._tool_manager.get_tools())

    assert set(tools) == {
        "remember",
        "search_memories",
        "list_memories",
        "get_memory",
        "update_memory",
        "forget_memory",
    }
    assert tools["remember"].parameters["properties"] == {
        "content": {"type": "string"},
        "metadata": {"anyOf": [{"additionalProperties": True, "type": "object"}, {"type": "null"}], "default": None},
    }
    assert tools["remember"].parameters["required"] == ["content"]
    assert tools["search_memories"].parameters["properties"] == {
        "limit": {"default": 10, "type": "integer"},
        "query": {"type": "string"},
    }
    assert tools["search_memories"].parameters["required"] == ["query"]
    assert tools["list_memories"].parameters["properties"] == {"limit": {"default": 20, "type": "integer"}}
    assert "required" not in tools["list_memories"].parameters
    assert tools["get_memory"].parameters["properties"] == {"memory_id": {"type": "string"}}
    assert tools["get_memory"].parameters["required"] == ["memory_id"]
    assert tools["update_memory"].parameters["properties"] == {
        "content": {"type": "string"},
        "memory_id": {"type": "string"},
        "metadata": {"anyOf": [{"additionalProperties": True, "type": "object"}, {"type": "null"}], "default": None},
    }
    assert tools["update_memory"].parameters["required"] == ["memory_id", "content"]
    assert tools["forget_memory"].parameters["properties"] == {"memory_id": {"type": "string"}}
    assert tools["forget_memory"].parameters["required"] == ["memory_id"]


def test_mcp_tools_coerce_numeric_string_limits(monkeypatch):
    """MCP clients commonly serialize numeric arguments as strings."""
    calls = []

    class Gateway:
        def search_memories(self, query, limit):
            calls.append(("search", query, limit, type(limit)))
            return {"ok": True, "memories": []}

        def list_memories(self, limit):
            calls.append(("list", limit, type(limit)))
            return {"ok": True, "memories": []}

        def get_memory(self, memory_id):
            return {"ok": True, "memory": {"id": memory_id}}

    monkeypatch.setattr(mcp_server, "_gateway_for_current_request", lambda: Gateway())
    server, _ = mcp_server.create_mcp_http_app()

    asyncio.run(server._tool_manager.call_tool("search_memories", {"query": "test", "limit": "3"}))
    asyncio.run(server._tool_manager.call_tool("list_memories", {"limit": "4"}))

    assert calls == [("search", "test", 3, int), ("list", 4, int)]


def test_valid_mcp_transport_reaches_the_mounted_application_and_invalid_transport_does_not(monkeypatch):
    """Bypassing the mounted boundary would let unauthenticated traffic reach MCP handlers."""
    reached = []

    async def inner(scope, receive, send):
        reached.append(scope[mcp_auth.MCP_PRINCIPAL_SCOPE_KEY])
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def authenticate(request):
        if request.headers.get("authorization") == "Bearer valid-account-key":
            return MemoryPrincipal(owner_id="00000000-0000-0000-0000-000000000111")
        raise HTTPException(status_code=401, detail="Authentication required.", headers={"WWW-Authenticate": "Bearer"})

    monkeypatch.setattr(mcp_auth, "require_mcp_bearer", authenticate)
    monkeypatch.setattr(mcp_server.mcp_authenticated_app, "app", inner)
    monkeypatch.setattr(main, "_should_log_request", lambda _request: False)
    client = TestClient(main.app, raise_server_exceptions=False)

    invalid = client.post("/mcp")
    valid = client.post("/mcp", headers={"Authorization": "Bearer valid-account-key"})

    assert invalid.status_code == 401
    assert invalid.headers["www-authenticate"] == "Bearer"
    assert valid.status_code == 204
    assert reached == [MemoryPrincipal(owner_id="00000000-0000-0000-0000-000000000111")]


def test_valid_bearer_transport_reaches_fastmcp_streamable_http(monkeypatch):
    """Mounting the inner app at /mcp/mcp would make this MCP initialization unreachable."""

    async def authenticate(_request):
        return MemoryPrincipal(owner_id="00000000-0000-0000-0000-000000000111")

    monkeypatch.setattr(mcp_auth, "require_mcp_bearer", authenticate)
    _, mcp_http_app = mcp_server.create_mcp_http_app()
    app = FastAPI(lifespan=mcp_http_app.lifespan)
    app.mount("/mcp", mcp_auth.MCPBearerAuthMiddleware(mcp_http_app))

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/mcp/",
            headers={
                "Authorization": "Bearer valid-account-key",
                "Accept": "application/json, text/event-stream",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "ram0-test", "version": "1"},
                },
            },
        )

    assert response.status_code == 200, response.text
    data_line = next(line.removeprefix("data: ") for line in response.text.splitlines() if line.startswith("data: "))
    payload = json.loads(data_line)
    assert payload["result"]["serverInfo"]["name"] == "Ram0"


def test_mounted_mcp_protocol_exposes_the_documented_six_tools(monkeypatch):
    """The documented slashless URL must support a complete authenticated MCP exchange."""

    class Gateway:
        def list_memories(self, limit):
            return {"ok": True, "memories": [], "limit": limit}

    async def authenticate(_request):
        return MemoryPrincipal(owner_id="00000000-0000-0000-0000-000000000111")

    monkeypatch.setattr(mcp_auth, "require_mcp_bearer", authenticate)
    monkeypatch.setattr(mcp_server, "_gateway_for_current_request", lambda: Gateway())
    monkeypatch.setattr(main, "_should_log_request", lambda _request: False)
    headers = {
        "Authorization": "Bearer valid-account-key",
        "Accept": "application/json, text/event-stream",
    }

    def request(client, path, payload, *, session_id=None):
        request_headers = headers.copy()
        if session_id:
            request_headers["mcp-session-id"] = session_id
        response = client.post(path, headers=request_headers, json=payload)
        assert response.status_code == 200, response.text
        data_line = next(
            line.removeprefix("data: ") for line in response.text.splitlines() if line.startswith("data: ")
        )
        return response, json.loads(data_line)

    with TestClient(main.app, raise_server_exceptions=False) as client:
        initialize, initialized = request(
            client,
            "/mcp",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "ram0-test", "version": "1"},
                },
            },
        )
        session_id = initialize.headers["mcp-session-id"]
        _, listed_tools = request(
            client,
            "/mcp",
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            session_id=session_id,
        )
        _, listed_memories = request(
            client,
            "/mcp",
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "list_memories", "arguments": {"limit": 1}},
            },
            session_id=session_id,
        )
        duplicate_path = client.post("/mcp/mcp", headers=headers)

    assert initialized["result"]["serverInfo"]["name"] == "Ram0"
    assert [response.status_code for response in initialize.history] == [307]
    assert initialize.history[0].headers["location"] == "/mcp/"
    expected_tools = {
        "remember",
        "search_memories",
        "list_memories",
        "get_memory",
        "update_memory",
        "forget_memory",
    }
    assert len(listed_tools["result"]["tools"]) == 6
    assert {tool["name"] for tool in listed_tools["result"]["tools"]} == expected_tools
    assert json.loads(listed_memories["result"]["content"][0]["text"]) == {"ok": True, "memories": [], "limit": 1}
    assert duplicate_path.status_code == 404


def test_mounted_mcp_protocol_serializes_a_guided_tool_error(monkeypatch):
    """MCP clients need a stable wire error instead of an opaque FastMCP failure."""
    from mcp_contract import tool_error

    class Gateway:
        def search_memories(self, query, limit):
            tool_error("invalid_argument", variant="search_query")

    async def authenticate(_request):
        return MemoryPrincipal(owner_id="00000000-0000-0000-0000-000000000111")

    monkeypatch.setattr(mcp_auth, "require_mcp_bearer", authenticate)
    monkeypatch.setattr(mcp_server, "_gateway_for_current_request", lambda: Gateway())
    monkeypatch.setattr(main, "_should_log_request", lambda _request: False)
    headers = {"Authorization": "Bearer valid-account-key", "Accept": "application/json, text/event-stream"}

    def request(client, payload, session_id=None):
        request_headers = {**headers, **({"mcp-session-id": session_id} if session_id else {})}
        response = client.post("/mcp", headers=request_headers, json=payload)
        assert response.status_code == 200, response.text
        data_line = next(
            line.removeprefix("data: ") for line in response.text.splitlines() if line.startswith("data: ")
        )
        return response, json.loads(data_line)

    with TestClient(main.app, raise_server_exceptions=False) as client:
        initialized_response, _ = request(
            client,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "ram0-test", "version": "1"},
                },
            },
        )
        _, result = request(
            client,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "search_memories", "arguments": {"query": ""}},
            },
            initialized_response.headers["mcp-session-id"],
        )

    assert result["result"]["isError"] is True
    assert json.loads(result["result"]["content"][0]["text"]) == {
        "code": "invalid_argument",
        "example": {"limit": 10, "query": "find my preferences"},
        "how_to_fix": "Provide a non-empty query string and an integer limit from 1 through 100.",
        "message": "query must be a non-empty string.",
        "ok": False,
    }


def test_mcp_slashless_redirect_does_not_leak_a_principal_to_the_next_request(monkeypatch):
    """The mounted request authenticates again, so the redirect scope needs no owner state."""
    request = mcp_auth.Request({"type": "http", "method": "POST", "path": "/mcp", "headers": []})

    async def authenticate(_request):
        return MemoryPrincipal(owner_id="00000000-0000-0000-0000-000000000111")

    monkeypatch.setattr(mcp_auth, "require_mcp_bearer", authenticate)
    response = asyncio.run(main.mcp_root(request))

    assert response.status_code == 307
    assert mcp_auth.MCP_PRINCIPAL_SCOPE_KEY not in request.scope


def test_main_mcp_lifespan_can_restart_for_a_new_test_client(monkeypatch):
    """A shared one-shot FastMCP session manager breaks the wider server test suite."""
    monkeypatch.setattr(main, "_should_log_request", lambda _request: False)

    with TestClient(main.app, raise_server_exceptions=False):
        pass
    with TestClient(main.app, raise_server_exceptions=False):
        pass


def test_mcp_readme_documents_the_protected_client_contract():
    """The setup guide must not cause clients to use the REST API-key header or a duplicate path."""
    content = (Path(__file__).parents[2] / "server/README.md").read_text()

    assert (
        '[mcp_servers.ram0]\nurl = "https://ram0.example.lan/mcp"\nbearer_token_env_var = "RAM0_API_KEY"'
    ) in content
    assert "Bearer-only" in content
    assert "does not accept\n`X-API-Key`" in content
    assert "account-wide" in content
    assert "ownership version 1" in content
    assert {
        "`remember`",
        "`search_memories`",
        "`list_memories`",
        "`get_memory`",
        "`update_memory`",
        "`forget_memory`",
    } <= set(part for part in content.split())


def test_public_ram0_mcp_documentation_is_registered_without_a_literal_credential():
    """The self-hosted MCP boundary needs public discoverability, not only server-local setup notes."""
    root = Path(__file__).parents[2]
    content = (root / "docs/open-source/ram0-mcp.mdx").read_text()
    docs_config = (root / "docs/docs.json").read_text()
    llms_index = (root / "docs/llms.txt").read_text()

    assert 'bearer_token_env_var = "RAM0_API_KEY"' in content
    assert "X-API-Key" in content
    assert "expiration_date" in content
    assert "open-source/ram0-mcp" in docs_config
    assert "Self-Hosted Ram0 MCP" in llms_index
