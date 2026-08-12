"""FastMCP Streamable HTTP application mounted by the REST server."""

from typing import Any

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request

from mcp_auth import MCP_PRINCIPAL_SCOPE_KEY, MCPBearerAuthMiddleware
from mcp_contract import tool_error
from mcp_service import Ram0McpGateway
from memory_authorization import MemoryPrincipal
from server_state import get_memory_instance


def _gateway_for_current_request() -> Ram0McpGateway:
    """Build a gateway from the immutable principal set by the outer transport."""
    principal = get_http_request().scope.get(MCP_PRINCIPAL_SCOPE_KEY)
    if not isinstance(principal, MemoryPrincipal):
        tool_error("authentication_required")
    return Ram0McpGateway(principal, get_memory_instance())


def create_mcp_http_app():
    """Create the root Streamable HTTP application mounted by FastAPI at /mcp."""
    server = FastMCP(
        "Ram0",
        mask_error_details=True,
        strict_input_validation=False,
    )

    @server.tool()
    def search_memories(query: str, limit: int = 10) -> dict[str, Any]:
        """Search your memories using a natural-language query."""
        return _gateway_for_current_request().search_memories(query, limit)

    @server.tool()
    def list_memories(limit: int = 20) -> dict[str, Any]:
        """List your memories."""
        return _gateway_for_current_request().list_memories(limit)

    @server.tool()
    def get_memory(memory_id: str) -> dict[str, Any]:
        """Retrieve one of your memories by its UUID."""
        return _gateway_for_current_request().get_memory(memory_id)

    @server.tool()
    def remember(content: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        """Remember one piece of information from a user-authored message."""
        return _gateway_for_current_request().remember(content, metadata)

    @server.tool()
    def update_memory(memory_id: str, content: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        """Update one of your memories."""
        return _gateway_for_current_request().update_memory(memory_id, content, metadata)

    @server.tool()
    def forget_memory(memory_id: str) -> dict[str, Any]:
        """Forget one of your memories by its UUID."""
        return _gateway_for_current_request().forget_memory(memory_id)

    # The outer FastAPI application mounts this inner root at /mcp. Keeping
    # this root path avoids a second, unreachable /mcp segment (/mcp/mcp).
    return server, server.http_app(path="/")


mcp, mcp_http_app = create_mcp_http_app()
mcp_authenticated_app = MCPBearerAuthMiddleware(mcp_http_app)
