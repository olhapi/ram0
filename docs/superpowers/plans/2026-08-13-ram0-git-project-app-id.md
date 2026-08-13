<!-- SPDX-FileCopyrightText: 2026 Ram0 contributors -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Ram0 Git Project `app_id` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Ram0 automatically store memories under the current Git repository and normally recall that repository's memories plus global account memories, without adding a projects subsystem or weakening account isolation.

**Architecture:** Add `app_id` as an account-local vector payload field governed by a small server-side scope policy. The local plugin derives a stable Git identity and supplies it to REST/MCP; the remote server always composes it beneath the authenticated owner. Existing memories with no `app_id` are global and need no rewrite.

**Tech Stack:** Python 3.10+, FastAPI, FastMCP, Mem0 OSS Python memory/vector payloads, pytest, shell/JSON lifecycle hooks, Next.js 15/React 19/TypeScript, pnpm 10, PostgreSQL/pgvector real-stack verifier.

## Global Constraints

- `user_id` remains server-derived from the authenticated account UUID on every surface.
- `app_id` is an organization filter inside one account, never an ownership or authorization boundary.
- Default reads return current-project plus global; project reads return current-project only; global reads return all memories owned by the account.
- Default and project writes require and store current `app_id`; global writes omit `app_id`.
- Existing memories with no `app_id` are global; do not rewrite them.
- Do not add project tables, APIs, members, settings, per-project categories, dependencies, or CI workflow changes.
- Add SPDX headers to every new Ram0-owned source/test/doc file and the Ram0 modification notice to materially modified upstream source files that support comments.
- Preserve indistinguishable missing/foreign 404 responses, redacted errors, category owner fences, backup/restore behavior, and the six-tool MCP surface.
- Use pnpm for dashboard work. Use focused tests, then package suites, then the disposable real-stack verifier.
- Preserve unrelated `.pnpm-store/`, `docs/research/`, and any other user changes.

---

### Task 1: Define the server-side app scope policy

**Files:**
- Create: `server/app_scope.py`
- Create: `tests/server/test_app_scope.py`
- Modify: `server/memory_authorization.py`
- Modify: `tests/server/test_memory_authorization.py`

**Interfaces:**
- Consumes: `MemoryPrincipal.owner_id` and Mem0 structured filter dictionaries.
- Produces: `AppScope`, `validate_app_id()`, `read_filters()`, `write_app_id()`, and `owner_filters(..., app_id=...)` for every later task.

- [ ] **Step 1: Write failing validation and scope tests**

Create `tests/server/test_app_scope.py` with the license header and these contract cases:

```python
import pytest

from app_scope import AppScope, read_filters, validate_app_id, write_app_id


@pytest.mark.parametrize("value", ["github.com-olhapi-ram0", "git.home.olhapi.com-oleh-bet", "local-repo_2"])
def test_validate_app_id_accepts_normalized_git_ids(value):
    assert validate_app_id(value) == value


@pytest.mark.parametrize("value", ["", " ", "/Users/oleh/ram0", "../ram0", "a" * 129])
def test_validate_app_id_rejects_unsafe_or_ambiguous_values(value):
    with pytest.raises(ValueError, match="app_id"):
        validate_app_id(value)


def test_default_read_is_owner_and_project_or_global():
    assert read_filters("owner-a", "github.com-olhapi-ram0", None) == {
        "AND": [
            {"user_id": "owner-a"},
            {"OR": [{"app_id": "github.com-olhapi-ram0"}, {"app_id": None}]},
        ]
    }


def test_explicit_scopes_are_exact():
    assert read_filters("owner-a", "app-a", AppScope.PROJECT) == {
        "AND": [{"user_id": "owner-a"}, {"app_id": "app-a"}]
    }
    assert read_filters("owner-a", None, AppScope.GLOBAL) == {"user_id": "owner-a"}
    assert write_app_id("app-a", None) == "app-a"
    assert write_app_id("app-a", AppScope.PROJECT) == "app-a"
    assert write_app_id(None, AppScope.GLOBAL) is None


def test_project_context_is_required_and_global_rejects_app_id():
    with pytest.raises(ValueError, match="project context"):
        read_filters("owner-a", None, None)
    with pytest.raises(ValueError, match="global"):
        write_app_id("app-a", AppScope.GLOBAL)
```

Define the accepted `app_id` contract as 1-128 ASCII characters matching `[A-Za-z0-9][A-Za-z0-9._-]*`; normalization belongs to the plugin resolver, not the server.

- [ ] **Step 2: Run the new test to prove RED**

```bash
.venv/bin/pytest tests/server/test_app_scope.py -q
```

Expected: collection fails because `app_scope` does not exist.

- [ ] **Step 3: Implement the focused policy module**

Create `server/app_scope.py` with SPDX and Ram0 notice:

```python
from enum import Enum
import re
from typing import Any

APP_ID_MAX_LENGTH = 128
_APP_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")


class AppScope(str, Enum):
    PROJECT = "project"
    GLOBAL = "global"


def validate_app_id(value: str | None) -> str:
    if not isinstance(value, str) or len(value) > APP_ID_MAX_LENGTH or not _APP_ID.fullmatch(value):
        raise ValueError("app_id must be a non-empty normalized project identifier.")
    return value


def read_filters(owner_id: str, app_id: str | None, scope: AppScope | None) -> dict[str, Any]:
    if scope is AppScope.GLOBAL:
        if app_id is not None:
            raise ValueError("global scope does not accept app_id.")
        return {"user_id": owner_id}
    if app_id is None:
        raise ValueError("project context is required.")
    current = validate_app_id(app_id)
    if scope is AppScope.PROJECT:
        return {"AND": [{"user_id": owner_id}, {"app_id": current}]}
    return {"AND": [{"user_id": owner_id}, {"OR": [{"app_id": current}, {"app_id": None}]}]}


def write_app_id(app_id: str | None, scope: AppScope | None) -> str | None:
    if scope is AppScope.GLOBAL:
        if app_id is not None:
            raise ValueError("global scope does not accept app_id.")
        return None
    if app_id is None:
        raise ValueError("project context is required.")
    return validate_app_id(app_id)
```

If Qdrant/pgvector represents missing fields differently from `{app_id: None}`, adjust only the filter-building representation and add an adapter-level test; do not change the public semantics.

- [ ] **Step 4: Extend owner filter composition without permitting owner override**

Add optional `app_id` to `owner_filters()` in `server/memory_authorization.py`, validated through `validate_app_id()`. Keep `reject_client_owner(extra)` and add the app clause before `extra`; reject `app_id` inside `extra` so callers cannot replace the trusted argument.

Add tests proving `user_id` is always present, `app_id` can only narrow, and nested client `user_id` remains rejected.

- [ ] **Step 5: Run focused policy tests and commit**

```bash
.venv/bin/pytest tests/server/test_app_scope.py tests/server/test_memory_authorization.py -q
git add server/app_scope.py server/memory_authorization.py tests/server/test_app_scope.py tests/server/test_memory_authorization.py
git commit -m "feat(server): define account-local app scopes"
```

Expected: all named tests pass.

---

### Task 2: Carry `app_id` through REST memories and entities

**Files:**
- Modify: `server/main.py`
- Modify: `server/routers/entities.py`
- Modify: `tests/server/test_memory_routes_authorization.py`
- Modify: `tests/server/test_owner_scoped_surfaces.py`
- Modify: `tests/server/test_category_memory_routes.py`

**Interfaces:**
- Consumes: `validate_app_id()` and owner-scoped filter composition from Task 1.
- Produces: REST create/search/list/delete support and account-local `app` entities for MCP/plugin/dashboard consumers.

- [ ] **Step 1: Add failing REST scope matrix tests**

Add tests using the existing authenticated-client and fake-memory fixtures:

```python
def test_create_passes_valid_app_id_beneath_derived_owner(client, memory, auth_headers):
    response = client.post(
        "/memories",
        headers=auth_headers,
        json={"messages": [{"role": "user", "content": "Decision: Use pgvector."}], "app_id": "github.com-olhapi-ram0"},
    )
    assert response.status_code == 200
    assert memory.add.call_args.kwargs["user_id"] == OWNER_ID
    assert memory.add.call_args.kwargs["app_id"] == "github.com-olhapi-ram0"


def test_search_with_app_id_never_loses_owner_filter(client, memory, auth_headers):
    response = client.post(
        "/search",
        headers=auth_headers,
        json={"query": "pgvector", "filters": {"app_id": "github.com-olhapi-ram0"}},
    )
    assert response.status_code == 200
    assert memory.search.call_args.kwargs["filters"] == {
        "user_id": OWNER_ID,
        "app_id": "github.com-olhapi-ram0",
    }
```

Also cover invalid/metadata `app_id` as 422, list/delete-all owner composition, returned payload preservation, and two accounts sharing the same app label.

- [ ] **Step 2: Run named REST tests to prove RED**

```bash
.venv/bin/pytest tests/server/test_memory_routes_authorization.py tests/server/test_owner_scoped_surfaces.py -q
```

Expected: failures show `MemoryCreate` rejects/drops `app_id` and entities omit `app`.

- [ ] **Step 3: Implement REST transport and reserved-field rules**

In `server/main.py`:

- Add `app_id: Optional[str]` to `MemoryCreate` and `SearchRequest`.
- Validate it at the route boundary.
- Pass it as a top-level Mem0 argument on create, never as metadata.
- Add it to `_MEMORY_FILTER_FIELDS`/response projection wherever `agent_id` and `run_id` are carried.
- Compose it with owner on list/search/delete-all.
- Reject nested metadata/filter attempts that conflict with a trusted top-level app argument.

Do not change the account-derived `user_id` behavior.

- [ ] **Step 4: Add app entities**

Change `server/routers/entities.py` to:

```python
EntityType = Literal["app", "agent", "run"]
TYPE_TO_FIELD: dict[EntityType, str] = {
    "app": "app_id",
    "agent": "agent_id",
    "run": "run_id",
}
```

Test counts/timestamps, owner-only scanning, and deletion calling `delete_all(user_id=principal.owner_id, app_id=entity_id)`.

- [ ] **Step 5: Prove category/update paths preserve the field**

Extend category memory route tests so a vector payload with `app_id` retains it after classification, update, failure, and reclassification preparation. Fix only projection code that drops the field; category catalogs remain account-wide.

- [ ] **Step 6: Run focused REST tests and commit**

```bash
.venv/bin/pytest tests/server/test_memory_routes_authorization.py tests/server/test_owner_scoped_surfaces.py tests/server/test_category_memory_routes.py -q
git add server/main.py server/routers/entities.py tests/server/test_memory_routes_authorization.py tests/server/test_owner_scoped_surfaces.py tests/server/test_category_memory_routes.py
git commit -m "feat(server): persist app-scoped memories"
```

---

### Task 3: Add default, project, and global MCP scopes

**Files:**
- Modify: `server/mcp_service.py`
- Modify: `server/mcp_server.py`
- Modify: `server/mcp_contract.py`
- Modify: `tests/server/test_mcp_service.py`
- Modify: `tests/server/test_mcp_server.py`
- Modify: `tests/server/test_mcp_contract.py`

**Interfaces:**
- Consumes: `AppScope`, `read_filters()`, `write_app_id()`.
- Produces: the exact six-tool MCP contract with optional `scope`/`app_id` on remember, search, and list.

- [ ] **Step 1: Write failing gateway tests for all scope combinations**

Add parameterized assertions:

```python
@pytest.mark.parametrize(
    ("scope", "app_id", "expected"),
    [
        (None, "app-a", {"AND": [{"user_id": OWNER}, {"OR": [{"app_id": "app-a"}, {"app_id": None}]}]}),
        ("project", "app-a", {"AND": [{"user_id": OWNER}, {"app_id": "app-a"}]}),
        ("global", None, {"user_id": OWNER}),
    ],
)
def test_search_scope_matrix(gateway, memory, scope, app_id, expected):
    gateway.search_memories("query", scope=scope, app_id=app_id)
    assert memory.search.call_args.kwargs["filters"] == expected
```

Add remember tests showing default/project pass `app_id`, global omits it, missing context fails safely, global plus app ID is rejected, invalid scopes return `invalid_argument`, and metadata cannot shadow `app_id`.

- [ ] **Step 2: Run MCP tests to prove RED**

```bash
.venv/bin/pytest tests/server/test_mcp_service.py tests/server/test_mcp_server.py tests/server/test_mcp_contract.py -q
```

- [ ] **Step 3: Implement gateway scope parsing and filtering**

Add a private parser that accepts `None`, `"project"`, or `"global"`, mapping stable validation failures to the existing safe MCP `invalid_argument` variants. Update signatures:

```python
def search_memories(self, query: str, limit: int = 10, scope: str | None = None, app_id: str | None = None) -> dict[str, Any]
def list_memories(self, limit: int = 20, scope: str | None = None, app_id: str | None = None) -> dict[str, Any]
def remember(self, content: str, metadata: dict[str, Any] | None = None, scope: str | None = None, app_id: str | None = None) -> dict[str, Any]
```

Default search/list use `read_filters`; remember uses `write_app_id` and adds top-level `app_id` only when non-null. Retain category owner fencing and notices.

- [ ] **Step 4: Expose arguments on the same six FastMCP tools**

Update only the three functions in `server/mcp_server.py`; do not add tools. Update descriptions to define default/project/global behavior without claiming the server detects Git.

- [ ] **Step 5: Run MCP tests and commit**

```bash
.venv/bin/pytest tests/server/test_mcp_service.py tests/server/test_mcp_server.py tests/server/test_mcp_contract.py -q
git add server/mcp_service.py server/mcp_server.py server/mcp_contract.py tests/server/test_mcp_service.py tests/server/test_mcp_server.py tests/server/test_mcp_contract.py
git commit -m "feat(mcp): add Git project memory scopes"
```

---

### Task 4: Resolve Git project identity in the Ram0 plugin

**Files:**
- Create: `integrations/ram0-plugin/scripts/project_scope.py`
- Create: `integrations/ram0-plugin/tests/test_project_scope.py`
- Modify: `integrations/ram0-plugin/scripts/ram0_config.py`
- Modify: `integrations/ram0-plugin/scripts/ram0_client.py`
- Modify: `integrations/ram0-plugin/tests/test_ram0_client.py`

**Interfaces:**
- Consumes: local cwd/environment/Git configuration and REST scope contract.
- Produces: `ProjectContext(app_id, root, branch)`, `resolve_project_context()`, and project-aware `Ram0Client` methods.

- [ ] **Step 1: Write resolver RED tests with real temporary Git repositories**

Cover this exact priority and normalization:

```python
def test_https_and_ssh_origin_resolve_to_same_host_qualified_id(tmp_path, monkeypatch):
    https = git_repo(tmp_path / "https", "https://github.com/olhapi/ram0.git")
    ssh = git_repo(tmp_path / "ssh", "git@github.com:olhapi/ram0.git")
    assert resolve_project_context(https).app_id == "github.com-olhapi-ram0"
    assert resolve_project_context(ssh).app_id == "github.com-olhapi-ram0"


def test_other_host_does_not_collide(tmp_path):
    repo = git_repo(tmp_path / "repo", "git@git.home.olhapi.com:olhapi/ram0.git")
    assert resolve_project_context(repo).app_id == "git.home.olhapi.com-olhapi-ram0"
```

Also test `RAM0_PROJECT_ID`, protected mapping state, moved clone remote-hash lookup, Git worktree equivalence, root-name fallback, cwd fallback, 128-character deterministic shortening, and empty-context failure.

- [ ] **Step 2: Run resolver tests to prove RED**

```bash
.venv/bin/pytest integrations/ram0-plugin/tests/test_project_scope.py -q
```

- [ ] **Step 3: Implement a pure resolver seam**

`project_scope.py` must:

- invoke Git with argument arrays and explicit `cwd`, never a shell;
- canonicalize scheme/SSH remote forms to lowercase host plus case-preserved owner/repo path;
- strip credentials, query, fragment, port, and `.git`;
- slug unsafe characters to `-`;
- truncate with a SHA-256 suffix so IDs remain deterministic and at most 128 characters;
- save aliases in the configured Ram0 plugin state directory with mode `0600` and atomic replace;
- never include the full remote or local path in server payloads/log output.

- [ ] **Step 4: Make the REST adapter scope-aware**

Change client APIs to explicit keyword-only scope context:

```python
def search(self, query: str, limit: int = 10, *, app_id: str, scope: str | None = None) -> Any
def list(self, limit: int = 100, *, app_id: str, scope: str | None = None) -> Any
def add(self, memory_text: str, metadata=None, *, app_id: str | None, scope: str | None = None) -> Any
def add_durable(self, memory_text: str, metadata=None, *, app_id: str | None, scope: str | None = None) -> Any
```

Default reads send the structured current-plus-global filter, project reads send the exact app filter, and global reads send no app filter. Default/project writes send top-level `app_id`; global writes omit it. Remove `app_id` from the generic reserved-body rejection only for these trusted top-level constructors; keep it forbidden recursively in user metadata.

- [ ] **Step 5: Run plugin resolver/client tests and commit**

```bash
.venv/bin/pytest integrations/ram0-plugin/tests/test_project_scope.py integrations/ram0-plugin/tests/test_ram0_client.py -q
git add integrations/ram0-plugin/scripts/project_scope.py integrations/ram0-plugin/scripts/ram0_config.py integrations/ram0-plugin/scripts/ram0_client.py integrations/ram0-plugin/tests/test_project_scope.py integrations/ram0-plugin/tests/test_ram0_client.py
git commit -m "feat(plugin): resolve Git project memory scope"
```

---

### Task 5: Apply project scope to lifecycle retrieval, capture, and skills

**Files:**
- Modify: `integrations/ram0-plugin/scripts/memory_capture.py`
- Modify: `integrations/ram0-plugin/scripts/hook_entry.py`
- Modify: `integrations/ram0-plugin/tests/test_memory_capture.py`
- Modify: `integrations/ram0-plugin/tests/test_hooks.py`
- Modify: `integrations/ram0-plugin/tests/test_source_boundaries.py`
- Modify: `integrations/ram0-plugin/tests/test_workflow_skills.py`
- Modify: `integrations/ram0-plugin/skills/ram0-memory/SKILL.md`
- Modify: `integrations/ram0-plugin/skills/remember/SKILL.md`
- Modify: `integrations/ram0-plugin/skills/peek/SKILL.md`
- Modify: `integrations/ram0-plugin/skills/tour/SKILL.md`
- Modify: other `integrations/ram0-plugin/skills/*/SKILL.md` files whose searches/writes must declare scope

**Interfaces:**
- Consumes: `resolve_project_context()` and scope-aware `Ram0Client`.
- Produces: automatic project writes, project-plus-global recall, explicit global workflow writes/searches, and host-safe context injection.

- [ ] **Step 1: Replace obsolete source-boundary assertions with failing scope assertions**

Tests must continue forbidding `user_id`, credentials, expiry controls, and `app_id` in metadata, while now requiring trusted top-level `app_id` from the resolver. Add:

```python
def test_automatic_retrieval_uses_current_project_plus_global(ram0_server, tmp_git_repo):
    run_hook("SessionStart", cwd=tmp_git_repo)
    assert ram0_server.last_search["filters"] == {
        "AND": [
            {"OR": [{"app_id": "github.com-olhapi-ram0"}, {"app_id": None}]}
        ]
    }


def test_automatic_capture_writes_current_project(ram0_server, tmp_git_repo):
    run_hook("Stop", cwd=tmp_git_repo, text="Decision: The adapter stays narrow.")
    assert ram0_server.last_add["app_id"] == "github.com-olhapi-ram0"
```

The server still derives owner, so plugin REST filters must not contain `user_id`.

- [ ] **Step 2: Run hook/capture tests to prove RED**

```bash
.venv/bin/pytest integrations/ram0-plugin/tests/test_memory_capture.py integrations/ram0-plugin/tests/test_hooks.py integrations/ram0-plugin/tests/test_source_boundaries.py -q
```

- [ ] **Step 3: Thread immutable project context through hook execution**

Resolve once per hook invocation from the host-provided cwd, then pass `context.app_id` to retrieval/capture. Include app ID in account-scoped capture deduplication so identical facts in two repositories can coexist. Do not put cwd, remote URL, branch, API key, or raw host input into memory metadata.

If a host changes cwd within a session, each new hook invocation resolves from that event's cwd instead of relying on stale process environment.

- [ ] **Step 4: Update skill contracts and instructions**

Change the skills from “API key selects the whole scope” to:

```text
Authentication selects the account. The plugin supplies the current app_id.
Normal reads use current project plus global memories.
Use scope="project" for repository-only reads.
Use scope="global" only when the user requests cross-project recall or an account-wide write.
Never supply user_id or place app_id in metadata.
```

Explicit `remember` defaults to project. It may use `scope="global"` only when the durable fact is clearly cross-project or the user asks. `peek` and `tour` expose explicit scope choices; destructive skills preview the exact resulting IDs before mutation.

- [ ] **Step 5: Run all Ram0 plugin tests and commit**

```bash
.venv/bin/pytest integrations/ram0-plugin/tests -q
git add integrations/ram0-plugin/scripts/memory_capture.py integrations/ram0-plugin/scripts/hook_entry.py integrations/ram0-plugin/tests integrations/ram0-plugin/skills
git commit -m "feat(plugin): scope memory lifecycle by Git project"
```

---

### Task 6: Show app scope in the dashboard

**Files:**
- Modify: `server/dashboard/src/types/api.ts`
- Modify: `server/dashboard/src/app/(root)/dashboard/memories/page.tsx`
- Modify: `server/dashboard/src/app/(root)/dashboard/entities/page.tsx`
- Create: `server/dashboard/tests/app-scope-rendering.test.ts`

**Interfaces:**
- Consumes: REST memory `app_id` and entity type `app`.
- Produces: read-only project/global visibility and bounded app-entity deletion through existing UI patterns.

- [ ] **Step 1: Add failing static/render contract tests**

Create a license-headed test that asserts:

```typescript
expect(apiTypes).toContain("app_id?: string");
expect(apiTypes).toContain('"app" | "agent" | "run"');
expect(memoriesPage).toContain('label: "Project"');
expect(memoriesPage).toContain("Global");
expect(entitiesPage).toContain('value="app"');
```

Also assert deletion still uses the existing entity endpoint and confirmation dialog rather than a new projects API.

- [ ] **Step 2: Run dashboard test to prove RED**

```bash
cd server/dashboard && node --experimental-strip-types --test tests/app-scope-rendering.test.ts
```

Expected: the Node static contract fails because the API types and pages do not yet expose app scope.

- [ ] **Step 3: Implement minimal visibility**

- Add `app_id?: string` to `Memory`.
- Change `EntityType` to `"app" | "agent" | "run"`; do not expose `user` because the server endpoint is already owner-derived.
- Add a Project column/detail displaying `app_id ?? "Global"`.
- Add an Apps filter/tab using the existing entities list and delete confirmation.
- Use “Project” in user copy and `app_id` only in technical details.

- [ ] **Step 4: Verify dashboard and commit**

```bash
cd server/dashboard && node --experimental-strip-types --test tests/app-scope-rendering.test.ts && pnpm run typecheck && pnpm run lint && pnpm run build
git add server/dashboard/src/types/api.ts 'server/dashboard/src/app/(root)/dashboard/memories/page.tsx' 'server/dashboard/src/app/(root)/dashboard/entities/page.tsx' server/dashboard/tests/app-scope-rendering.test.ts
git commit -m "feat(dashboard): show project memory scopes"
```

Expected: typecheck, Prettier check, build, and the new test pass.

---

### Task 7: Prove persistence, isolation, documentation, and release readiness

**Files:**
- Modify: `scripts/verify_multi_user_real_stack.py`
- Modify: `tests/server/test_real_stack_verifier_safety.py`
- Modify: `server/README.md`
- Modify: `docs/open-source/ram0-mcp.mdx`
- Modify: `docs/integrations/ram0-plugin.mdx`
- Modify: `docs/llms.txt` only if the coverage checker requires changed/new index text

**Interfaces:**
- Consumes: completed REST, MCP, plugin, entity, dashboard, backup, and category behavior.
- Produces: disposable PostgreSQL/pgvector evidence for account and project isolation plus current operator/user documentation.

- [ ] **Step 1: Add failing verifier source contracts**

Require the verifier to create, for both admin/member accounts:

```text
global memory
app-a memory
app-b memory
same-app-name memory owned by the other account
```

Require assertions for default/project/global reads, app entity counts/deletion, direct UUID foreign 404, category/reclassification preservation, reset owner isolation, and restored vector payloads retaining `app_id`/missing-global state.

Update the success marker expectation to:

```text
REAL_STACK_PASS migration=ready idempotent=true backup_restore=true logs_redacted=true app_scopes=true
```

- [ ] **Step 2: Run source-safety tests to prove RED**

```bash
.venv/bin/pytest tests/server/test_real_stack_verifier_safety.py -q
```

- [ ] **Step 3: Extend the disposable real-stack matrix**

Use unique safe app IDs per run. Verify both JWT and API-key auth. Inspect restored PostgreSQL payloads with bounded SQL that checks only IDs and `app_id`, never memory text or credentials. Preserve the unique temporary history database and existing cleanup guarantees.

- [ ] **Step 4: Update documentation**

Document:

- `app_id` is a Git-project grouping, not a Ram0 project resource or security boundary;
- default recall is current project plus global;
- default writes are project-scoped and explicit global writes are account-wide;
- resolution order and `RAM0_PROJECT_ID` override;
- direct MCP cannot auto-detect cwd;
- existing unscoped memories are global;
- app entities are derived buckets and deletion removes matching memories only inside the authenticated account.

Run `python scripts/check-llms-txt-coverage.py` and modify `docs/llms.txt` only if required.

- [ ] **Step 5: Run focused and full verification**

```bash
.venv/bin/pytest tests/server/test_app_scope.py tests/server/test_memory_authorization.py tests/server/test_memory_routes_authorization.py tests/server/test_owner_scoped_surfaces.py tests/server/test_mcp_service.py tests/server/test_mcp_server.py tests/server/test_mcp_contract.py tests/server/test_real_stack_verifier_safety.py -q
.venv/bin/pytest tests/server -q
.venv/bin/pytest integrations/ram0-plugin/tests -q
.venv/bin/pytest tests/memory -q
cd server/dashboard && pnpm run typecheck && pnpm run lint && pnpm run build
python scripts/check-llms-txt-coverage.py
python scripts/verify_multi_user_real_stack.py
git diff --check
```

Expected final verifier marker includes `app_scopes=true`; all touched-package checks pass. Report unrelated pre-existing baseline failures separately and do not weaken gates to accommodate them.

- [ ] **Step 6: Commit acceptance and documentation**

```bash
git add scripts/verify_multi_user_real_stack.py tests/server/test_real_stack_verifier_safety.py server/README.md docs/open-source/ram0-mcp.mdx docs/integrations/ram0-plugin.mdx docs/llms.txt
git commit -m "docs: verify Ram0 Git project memory scopes"
```

- [ ] **Step 7: Request final code review before integration**

Invoke `superpowers:requesting-code-review` against the implementation range. Require reviewers to check the approved design, account/app composition on every surface, default-plus-global semantics, resolver privacy, and real-stack evidence. Address findings before merge or deployment.
