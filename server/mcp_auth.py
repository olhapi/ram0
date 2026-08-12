"""Bearer-only authentication boundary for the Streamable HTTP MCP transport."""

import re
from collections.abc import Awaitable, Callable
from typing import Any

from auth import SessionLocal, _resolve_user_from_api_key
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from memory_authorization import MemoryPrincipal, principal_for
from memory_owner_migration import require_ownership_ready


MCP_PRINCIPAL_SCOPE_KEY = "mcp_principal"
_BEARER_CREDENTIAL = re.compile(r"Bearer [^\s]+$")


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail="Authentication required.",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def require_mcp_bearer(request: Request) -> MemoryPrincipal:
    """Resolve an active API-key owner from the MCP-only Bearer transport.

    REST supports several authentication modes, but MCP deliberately supports
    only a non-empty ``Bearer <API key>`` credential. The existing lookup
    performs the hashed comparison, key revocation check, and active-account
    check without exposing the key to logs or request scope.
    """
    if "x-api-key" in request.headers:
        raise _unauthorized()

    authorization = request.headers.get("authorization")
    if authorization is None or _BEARER_CREDENTIAL.fullmatch(authorization) is None:
        raise _unauthorized()

    key = authorization.removeprefix("Bearer ")
    try:
        with SessionLocal() as session:
            user = _resolve_user_from_api_key(key, session)
    except HTTPException:
        raise _unauthorized()

    require_ownership_ready()
    request.state.auth_type = "mcp_bearer_api_key"
    return principal_for(user)


class MCPBearerAuthMiddleware:
    """Authenticate every mounted MCP request before the FastMCP ASGI app."""

    def __init__(self, app: Callable[..., Awaitable[None]]):
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        try:
            principal = await require_mcp_bearer(Request(scope))
        except HTTPException as error:
            response = JSONResponse(
                status_code=error.status_code,
                content={"detail": error.detail},
                headers=error.headers,
            )
            await response(scope, receive, send)
            return

        scope[MCP_PRINCIPAL_SCOPE_KEY] = principal
        await self.app(scope, receive, send)
