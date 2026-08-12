# Ram0 FastMCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one bearer-authenticated, curated FastMCP Streamable HTTP endpoint to Ram0's existing FastAPI process.

**Architecture:** FastMCP is an ASGI sub-application mounted at `/mcp`. Its six tools call a narrow gateway that receives the account-derived `MemoryPrincipal`; an outer transport boundary accepts only an owned Ram0 API key in `Authorization: Bearer` and uses the existing ownership/readiness helpers rather than reimplementing `user_id` rules.

**Tech Stack:** Python 3.12, FastAPI, FastMCP 2.x, Pydantic v2, pytest, Starlette TestClient.

## Global Constraints

- Add exactly one Streamable HTTP MCP endpoint at `/mcp`; do not create a sidecar, CLI, OAuth flow, or browser sign-in.
- Accept only `Authorization: Bearer $RAM0_API_KEY`. Reject `X-API-Key` and every other credential format.
- `mcp_auth` resolves the existing hashed API-key owner, confirms that account is active, creates the existing immutable `MemoryPrincipal`, and requires ownership version 1 readiness before tools run.
- Never duplicate direct `user_id` filtering/policy in MCP. Use the principal-aware collection and record ownership helpers implemented for REST; the principal owner UUID is the canonical Mem0 owner.
- Expose only `remember`, `search_memories`, `list_memories`, `get_memory`, `update_memory`, and `forget_memory`. No tool schema exposes `user_id`, `agent_id`, `run_id`, or a scope selector.
- Do not expose REST administration, configuration, API-key management, request logs, reset, entity enumeration, or bulk deletion through MCP.
- Keep compatible input coercion enabled. Every Ram0-controlled failure is a guided MCP tool error; unexpected failures hide internal details.
- Preserve existing category processing for add, update, and delete. Never commit `RAM0_API_KEY`.

MCP is a follow-on feature and is unavailable until the multi-user ownership migration reports version 1 ready. A blocked or in-progress migration remains a maintenance failure, not a namespace that MCP may infer or bypass.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `server/requirements.txt` | FastMCP runtime dependency |
| `server/mcp_auth.py` | Bearer parsing, account API-key lookup, readiness, and principal creation |
| `server/mcp_contract.py` | Pydantic results plus stable guided errors |
| `server/mcp_service.py` | Principal-aware memory gateway and category hooks |
| `server/mcp_server.py` | FastMCP tool definitions and ASGI application |
| `server/main.py` | Lifespan composition and `/mcp` mount |
| `tests/server/test_mcp_auth.py` | Bearer-only transport, active owner, readiness |
| `tests/server/test_mcp_contract.py` | Schemas, error envelopes, and notices |
| `tests/server/test_mcp_service.py` | Principal-scoped CRUD and provider failure translation |
| `tests/server/test_mcp_server.py` | Streamable HTTP mounted-app round trip |
| `server/README.md` | Client setup and verification |

## Task 1: Bearer-only principal transport

**Files:** Create `server/mcp_auth.py`, `server/mcp_server.py`, and transport tests; modify `server/requirements.txt` and `server/main.py`.

- [ ] Write tests proving missing/malformed bearer credentials and `X-API-Key` return `401` with `WWW-Authenticate: Bearer`, a valid bearer key resolves its account owner, disabled/revoked keys fail, and migration-not-ready MCP traffic is unavailable.
- [ ] Run `rtk pytest tests/server/test_mcp_auth.py tests/server/test_mcp_server.py -q` and confirm the missing transport fails for the expected reason.
- [ ] Add `fastmcp>=2.14,<3`. Configure FastMCP with `mask_error_details=True` and compatible input coercion.
- [ ] Create the inner ASGI application with exactly `mcp.http_app(path="/")`, then mount that application at FastAPI `/mcp`. Assert the initialized Streamable HTTP route is exactly `/mcp` (with its protocol-supported trailing-slash behavior), never `/mcp/mcp`.
- [ ] Implement `require_mcp_bearer`: accept exactly `Bearer <non-empty-key>`, use the existing hashed key lookup and active-account check, call `require_ownership_ready`, and convert the resolved user with `principal_for`. Do not log the raw key.
- [ ] Apply the transport check to every `/mcp` path, retaining request-ID behavior; store only the immutable principal/owner in request scope. Compose FastMCP and category lifespans.
- [ ] Re-run the focused tests; valid bearer traffic reaches FastMCP and invalid transport never does.
- [ ] Commit the foundation with `feat(server): add bearer-authenticated MCP transport`.

## Task 2: Guided errors and scoped read tools

**Files:** Create `server/mcp_contract.py`, `server/mcp_service.py`, and their tests; modify `server/mcp_server.py` and mounted-route tests.

- [ ] Write failing tests proving tool schemas expose only documented arguments, numeric-string limits coerce, and missing/foreign records return the identical `memory_not_found` error.
- [ ] Run `rtk pytest tests/server/test_mcp_contract.py tests/server/test_mcp_service.py tests/server/test_mcp_server.py -q` and confirm it fails because the contract/gateway is absent.
- [ ] Define structured success/error results. Every expected failure is a `ToolError` containing stable JSON with `ok: false`, `code`, `message`, `how_to_fix`, and an example.
- [ ] Implement `Ram0McpGateway` with a `MemoryPrincipal`, not a raw owner argument. `search_memories` and `list_memories` call the shared owner-scoped helpers. `get_memory` calls the shared record-ownership helper. Do not use a parallel direct `user_id` policy.
- [ ] Map malformed UUIDs to `invalid_memory_id`; map missing and foreign records to the same `memory_not_found` guidance. Register only `search_memories(query, limit=10)`, `list_memories(limit=20)`, and `get_memory(memory_id)`.
- [ ] Re-run focused tests and commit with `feat(server): add scoped Ram0 MCP read tools`.

## Task 3: Write tools, notices, and safe provider errors

**Files:** Modify the MCP contract, gateway, server, and tests.

- [ ] Write failing tests proving `remember` preserves metadata and injects the principal owner, update/delete hide foreign records, provider quota yields non-retryable guidance, and unexpected errors omit secrets while carrying a request ID.
- [ ] Run the focused MCP tests and verify their expected failure.
- [ ] Implement `remember(content, metadata=None)`, `update_memory(memory_id, content, metadata=None)`, and `forget_memory(memory_id)` with the shared principal ownership helpers. `remember` passes one user message to the existing extraction path; updates/deletes use the existing category hooks.
- [ ] Retain the stable error codes: `invalid_argument`, `invalid_memory_id`, `memory_not_found`, `memory_write_unavailable`, `upstream_unavailable`, and `internal_error`. Map quota failures to one-retry-after-remediation guidance, connection/timeouts to one retry after five seconds, and all other exceptions to a request-ID-only internal error.
- [ ] Do not expose raw inference, prompt, expiration, category, filter, or identity inputs. Preserve best-effort category-hook notices in successful responses.
- [ ] Re-run focused tests and commit with `feat(server): add Ram0 MCP write tools`.

## Task 4: Documentation and Streamable HTTP proof

**Files:** Modify `server/README.md` and mounted MCP tests.

- [ ] Write a failing HTTP-level test that performs initialize, `list_tools`, and `list_memories` through the mounted route using a real bearer header; it must assert `/mcp` succeeds, `/mcp/mcp` does not exist, and exactly the six approved tools are visible. Add a README-content assertion for `/mcp`, bearer auth, `RAM0_API_KEY`, `bearer_token_env_var`, and the lack of `X-API-Key` support.
- [ ] Run `rtk pytest tests/server/test_mcp_server.py -q` and observe the expected missing documentation/endpoint failure.
- [ ] Document this protected client configuration without a literal key:

```toml
[mcp_servers.ram0]
url = "https://ram0.example.lan/mcp"
bearer_token_env_var = "RAM0_API_KEY"
```

  Document all six tools, the account-wide version-one namespace, bearer-only authentication, ownership-version readiness, and a real check: remember a preference, start a new task, then search for it. `RAM0_API_KEY` belongs only in the MCP client's protected environment.
- [ ] Run the mounted HTTP test and then:

```shell
rtk pytest tests/server/test_mcp_auth.py tests/server/test_mcp_contract.py tests/server/test_mcp_service.py tests/server/test_mcp_server.py -q
rtk pytest tests/server -q
rtk ruff check server/mcp_auth.py server/mcp_contract.py server/mcp_service.py server/mcp_server.py server/main.py tests/server/test_mcp_auth.py tests/server/test_mcp_contract.py tests/server/test_mcp_service.py tests/server/test_mcp_server.py
rtk ruff format --check server/mcp_auth.py server/mcp_contract.py server/mcp_service.py server/mcp_server.py server/main.py tests/server/test_mcp_auth.py tests/server/test_mcp_contract.py tests/server/test_mcp_service.py tests/server/test_mcp_server.py
```

- [ ] Confirm every command passes without printing a key and commit the documentation with `docs(server): document Ram0 MCP setup`.
