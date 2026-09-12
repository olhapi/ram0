# Supermemory Local Cutover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the deployed Mem0-derived Ram0 stack with a pinned Supermemory Local engine, a native on-demand MCP service, a graph explorer, and a lossless two-account memory migration.

**Architecture:** A Node gateway owns `/mcp`, health checks, and reverse-proxies the remaining REST API to an internal pinned `supermemory-server`. The gateway searches `personal` plus an optional repository container and exposes Supermemory-native MCP tools. A deterministic migration tool converts the two Ram0 account exports into resumable v4 memory imports; Unraid keeps the old PostgreSQL data untouched for rollback.

**Tech Stack:** TypeScript 5.8, Node.js 24, `@modelcontextprotocol/sdk`, Zod, Vitest, Supermemory REST API, Docker Compose, Next.js memory graph explorer, Bash deployment checks.

**Spec:** `docs/superpowers/specs/2026-09-12-supermemory-cutover-design.md`

## Global Constraints

- Pin `supermemory-server` version `0.0.6` and Linux x64 SHA-256 `bb1b7cee393818236873b8e2518a435e10d9195e27ea5608a3af48a733ef8ee8`.
- Preserve API/MCP host port `18888` and dashboard host port `13000`.
- Store Supermemory state in a new directory and never delete the old PostgreSQL or history directories.
- Require bearer authentication on every MCP request and never log credentials or memory content.
- Keep the new Git history directly based on `supermemoryai/supermemory/main`.
- Do not modify Cloudflare routes; verify them after preserving the same local origins.

---

### Task 1: Gateway configuration, authentication, and scope model

**Files:**
- Create: `apps/local-gateway/package.json`
- Create: `apps/local-gateway/tsconfig.json`
- Create: `apps/local-gateway/vitest.config.ts`
- Create: `apps/local-gateway/src/config.ts`
- Create: `apps/local-gateway/src/auth.ts`
- Create: `apps/local-gateway/src/scope.ts`
- Test: `apps/local-gateway/src/config.test.ts`
- Test: `apps/local-gateway/src/auth.test.ts`
- Test: `apps/local-gateway/src/scope.test.ts`

**Interfaces:**
- Produces: `loadConfig(env): GatewayConfig`, `authenticateBearer(header, expected): boolean`, `resolveContainers(explicitProject?): string[]`.
- `GatewayConfig` contains `host`, `port`, `engineUrl`, `apiKey`, and `personalContainer`.

- [ ] **Step 1: Write failing configuration and authorization tests**

```ts
expect(() => loadConfig({})).toThrow("SUPERMEMORY_API_KEY")
expect(authenticateBearer("Bearer secret", "secret")).toBe(true)
expect(authenticateBearer("Bearer wrong", "secret")).toBe(false)
expect(resolveContainers("repo_ram0__abc")).toEqual(["personal", "repo_ram0__abc"])
expect(resolveContainers()).toEqual(["personal"])
```

- [ ] **Step 2: Run the focused tests and confirm they fail**

Run: `docker run --rm -v "$PWD:/repo" -w /repo oven/bun:1.3.6 bun test apps/local-gateway/src/config.test.ts apps/local-gateway/src/auth.test.ts apps/local-gateway/src/scope.test.ts`

- [ ] **Step 3: Implement strict configuration, constant-time bearer comparison, and ordered unique scopes**

```ts
export interface GatewayConfig {
  host: string
  port: number
  engineUrl: string
  apiKey: string
  personalContainer: string
}

export function resolveContainers(project?: string): string[] {
  return project && project !== "personal" ? ["personal", project] : ["personal"]
}
```

- [ ] **Step 4: Run the focused tests and type checker**

Run: `docker run --rm -v "$PWD:/repo" -w /repo oven/bun:1.3.6 sh -lc 'bun install --frozen-lockfile && bun test apps/local-gateway/src && bunx tsc --noEmit -p apps/local-gateway/tsconfig.json'`

- [ ] **Step 5: Commit**

```bash
git add apps/local-gateway package.json bun.lock
git commit -m "feat(gateway): add secure local configuration"
```

### Task 2: Supermemory REST adapter and merged recall

**Files:**
- Create: `apps/local-gateway/src/backend.ts`
- Create: `apps/local-gateway/src/recall.ts`
- Test: `apps/local-gateway/src/backend.test.ts`
- Test: `apps/local-gateway/src/recall.test.ts`

**Interfaces:**
- Produces: `SupermemoryBackend`, `MemoryHit`, `Profile`, `recallAcrossScopes(backend, query, containers, options)`.
- Consumes: ordered containers from `resolveContainers`.

- [ ] **Step 1: Write failing adapter and merge tests**

```ts
const result = await recallAcrossScopes(fakeBackend, "deployment", ["personal", "repo_ram0__abc"], { limit: 5, includeProfile: true })
expect(result.results.map((item) => item.id)).toEqual(["project-high", "personal-medium"])
expect(result.containers).toEqual(["personal", "repo_ram0__abc"])
expect(result.profiles.personal.static).toContain("Prefers concise output")
```

- [ ] **Step 2: Run the focused tests and confirm they fail**

Run: `docker run --rm -v "$PWD:/repo" -w /repo oven/bun:1.3.6 bun test apps/local-gateway/src/backend.test.ts apps/local-gateway/src/recall.test.ts`

- [ ] **Step 3: Implement API calls and deterministic cross-container merge**

```ts
export interface MemoryBackend {
  search(query: string, containerTag: string, limit: number): Promise<MemoryHit[]>
  profile(query: string, containerTag: string): Promise<Profile>
  add(content: string, containerTag: string, metadata?: Record<string, unknown>): Promise<{ id: string }>
  forget(content: string, containerTag: string): Promise<{ id?: string; success: boolean }>
  listMemories(containerTag: string, limit: number): Promise<unknown>
  listDocuments(containerTag: string, limit: number): Promise<unknown>
  getDocument(id: string): Promise<unknown>
  listSpaces(): Promise<unknown>
}
```

Deduplicate by ID first and normalized text second, retain the strongest score,
sort descending by score, and truncate only after merging all containers.

- [ ] **Step 4: Run all gateway tests and type checking**

Run: `docker run --rm -v "$PWD:/repo" -w /repo oven/bun:1.3.6 sh -lc 'bun test apps/local-gateway/src && bunx tsc --noEmit -p apps/local-gateway/tsconfig.json'`

- [ ] **Step 5: Commit**

```bash
git add apps/local-gateway
git commit -m "feat(gateway): add Supermemory recall adapter"
```

### Task 3: Stateless Streamable HTTP MCP and REST proxy

**Files:**
- Create: `apps/local-gateway/src/mcp.ts`
- Create: `apps/local-gateway/src/proxy.ts`
- Create: `apps/local-gateway/src/server.ts`
- Test: `apps/local-gateway/src/mcp.test.ts`
- Test: `apps/local-gateway/src/server.test.ts`

**Interfaces:**
- Produces: `createMemoryMcpServer(deps)`, `createGatewayServer(config, backend)`, executable `src/server.ts`.
- Consumes: `MemoryBackend`, `recallAcrossScopes`, bearer authentication, and scope resolution.

- [ ] **Step 1: Write failing real-transport tests**

```ts
const response = await mcpClient.callTool({
  name: "search_memory",
  arguments: { query: "deployment", projectContainer: "repo_ram0__abc" },
})
expect(response.structuredContent.containers).toEqual(["personal", "repo_ram0__abc"])
expect(await unauthorizedMcpRequest()).toMatchObject({ status: 401 })
expect(await proxiedRequest("/v4/profile")).toMatchObject({ status: 200 })
```

- [ ] **Step 2: Run the focused tests and confirm they fail**

Run: `docker run --rm -v "$PWD:/repo" -w /repo oven/bun:1.3.6 bun test apps/local-gateway/src/mcp.test.ts apps/local-gateway/src/server.test.ts`

- [ ] **Step 3: Register the Supermemory-native MCP contract**

Register `search_memory`, `add_memory`, `list_memories`, `list_documents`,
`get_document`, `list_spaces`, and `who_am_i`, plus profile/spaces resources and
the `context` prompt. Use a stateless `StreamableHTTPServerTransport` per HTTP
request and close both transport and MCP server when the response closes.

- [ ] **Step 4: Implement authenticated routing and streaming REST proxy**

`/mcp` accepts `GET`, `POST`, and `DELETE`; `/health` reports gateway and engine
status without revealing configuration; every other path proxies method,
headers, query, body, status, and response stream to `engineUrl`.

- [ ] **Step 5: Run the gateway suite and type checker**

Run: `docker run --rm -v "$PWD:/repo" -w /repo oven/bun:1.3.6 sh -lc 'bun test apps/local-gateway/src && bunx tsc --noEmit -p apps/local-gateway/tsconfig.json'`

- [ ] **Step 6: Commit**

```bash
git add apps/local-gateway
git commit -m "feat(mcp): add on-demand Supermemory tools"
```

### Task 4: Local graph explorer and container images

**Files:**
- Modify: `apps/memory-graph-playground/src/app/api/graph/route.ts`
- Modify: `apps/memory-graph-playground/src/app/api/container-tags/route.ts`
- Create: `apps/local-gateway/Dockerfile`
- Create: `apps/memory-graph-playground/Dockerfile`
- Create: `deploy/supermemory/Dockerfile.engine`
- Test: `apps/memory-graph-playground/src/app/api/routes.test.ts`

**Interfaces:**
- Produces: immutable gateway, graph UI, and engine images.
- Engine build args: `SUPERMEMORY_SERVER_VERSION` and `SUPERMEMORY_SERVER_SHA256`.

- [ ] **Step 1: Write failing graph route tests for `SUPERMEMORY_API_BASE_URL`**

```ts
process.env.SUPERMEMORY_API_BASE_URL = "http://supermemory-engine:6767"
expect(capturedUrl.origin).toBe("http://supermemory-engine:6767")
```

- [ ] **Step 2: Run the graph route tests and confirm they fail**

Run: `docker run --rm -v "$PWD:/repo" -w /repo oven/bun:1.3.6 bun test apps/memory-graph-playground/src/app/api/routes.test.ts`

- [ ] **Step 3: Make the API origin configurable and add multi-stage images**

The engine Dockerfile downloads only the release asset matching the declared
version, verifies the fixed SHA-256, installs no shell-time secrets, runs as a
non-root user, and stores data beneath `/data`.

- [ ] **Step 4: Build all three images and inspect their configured users and architecture**

Run: `docker build -f deploy/supermemory/Dockerfile.engine -t ram0-engine:test .`

Run: `docker build -f apps/local-gateway/Dockerfile -t ram0-gateway:test .`

Run: `docker build -f apps/memory-graph-playground/Dockerfile -t ram0-graph:test .`

- [ ] **Step 5: Commit**

```bash
git add apps/local-gateway apps/memory-graph-playground deploy/supermemory
git commit -m "feat(deploy): package local Supermemory stack"
```

### Task 5: Deterministic Ram0 export normalization and import

**Files:**
- Create: `tools/ram0-migration/package.json`
- Create: `tools/ram0-migration/tsconfig.json`
- Create: `tools/ram0-migration/src/model.ts`
- Create: `tools/ram0-migration/src/plan.ts`
- Create: `tools/ram0-migration/src/import.ts`
- Create: `tools/ram0-migration/src/cli.ts`
- Test: `tools/ram0-migration/src/plan.test.ts`
- Test: `tools/ram0-migration/src/import.test.ts`
- Create: `tools/ram0-migration/fixtures/two-accounts.json`

**Interfaces:**
- Produces: `buildImportPlan(exports, mapping): ImportRecord[]`, `importPlan(plan, journal, backend): ImportSummary`.
- Import record contains deterministic `key`, `content`, `containerTag`, and namespaced provenance metadata.

- [ ] **Step 1: Write failing two-account deduplication and resume tests**

```ts
expect(plan.sourceCount).toBe(4)
expect(plan.records).toHaveLength(3)
expect(plan.records[0].metadata.ram0_source_ids).toEqual(["a-1", "b-7"])
expect(secondRun.imported).toBe(0)
expect(secondRun.skipped).toBe(3)
```

- [ ] **Step 2: Run focused migration tests and confirm they fail**

Run: `docker run --rm -v "$PWD:/repo" -w /repo oven/bun:1.3.6 bun test tools/ram0-migration/src`

- [ ] **Step 3: Implement validation, exact deduplication, mapping, and JSONL output**

Reject empty memory text and malformed source records into an explicit failure
list. Map absent `app_id` to `personal`; require every non-empty `app_id` in the
mapping file. Retain source account IDs, memory IDs, timestamps, categories,
and safe metadata under `ram0_*` keys.

- [ ] **Step 4: Implement batch v4 import with journaled idempotency**

Use batches no larger than 100. Append a journal entry only after the API
acknowledges the batch. On retry, skip deterministic keys already recorded as
successful and report imported, skipped, and failed counts.

- [ ] **Step 5: Run migration tests and type checker**

Run: `docker run --rm -v "$PWD:/repo" -w /repo oven/bun:1.3.6 sh -lc 'bun test tools/ram0-migration/src && bunx tsc --noEmit -p tools/ram0-migration/tsconfig.json'`

- [ ] **Step 6: Commit**

```bash
git add tools/ram0-migration package.json bun.lock
git commit -m "feat(migration): add resumable Ram0 importer"
```

### Task 6: Guarded Unraid deployment and rollback

**Files:**
- Create: `deploy/supermemory/compose.yaml`
- Create: `deploy/supermemory/.env.example`
- Create: `deploy/supermemory/deploy-unraid.sh`
- Create: `deploy/supermemory/verify-stack.sh`
- Test: `deploy/supermemory/tests/deploy-unraid.bats`
- Test: `deploy/supermemory/tests/verify-compose.sh`

**Interfaces:**
- Produces: a deployment command accepting a full Git SHA and immutable image references.
- Consumes: verified Git/data backup manifests and the completed import journal.

- [ ] **Step 1: Write failing static and mocked deployment checks**

Assert that Compose binds only `${RAM0_HOST_IP}:18888:8000` and
`${RAM0_HOST_IP}:13000:3000`, mounts a new Supermemory directory, never mounts
the Docker socket, and contains no command that deletes the legacy PostgreSQL
directory or uses `docker compose down -v`.

- [ ] **Step 2: Run the deployment tests and confirm they fail**

Run: `bash deploy/supermemory/tests/verify-compose.sh`

- [ ] **Step 3: Implement candidate deployment, health gates, promotion, and rollback**

The script creates a final live PostgreSQL dump, copies old Compose/state files,
starts the candidate on loopback verification ports, imports and verifies all
planned memories, stops only the old application containers, promotes the new
ports, verifies direct and public endpoints, and restores the old stack on any
post-mutation failure. It never removes volumes.

- [ ] **Step 4: Validate rendered Compose and shell syntax**

Run: `docker compose --env-file deploy/supermemory/.env.example -f deploy/supermemory/compose.yaml config --quiet`

Run: `bash -n deploy/supermemory/deploy-unraid.sh deploy/supermemory/verify-stack.sh`

- [ ] **Step 5: Commit**

```bash
git add deploy/supermemory
git commit -m "feat(deploy): add guarded Unraid cutover"
```

### Task 7: Claude Code and Codex installation contract

**Files:**
- Create: `integrations/coding-agents/README.md`
- Create: `integrations/coding-agents/install.mjs`
- Create: `integrations/coding-agents/project-scope.mjs`
- Test: `integrations/coding-agents/project-scope.test.ts`
- Test: `integrations/coding-agents/install.test.ts`

**Interfaces:**
- Produces: `resolveRepositoryContainer(cwd): string` and an idempotent installer for both clients.
- Consumes: public gateway URL and bearer-key environment-variable name without persisting the key in Git.

- [ ] **Step 1: Write failing repository identity and idempotent-install tests**

```ts
expect(resolveRepositoryContainer(cloneA)).toBe(resolveRepositoryContainer(cloneB))
expect(resolveRepositoryContainer(sameNameDifferentRemote)).not.toBe(resolveRepositoryContainer(cloneA))
expect(runInstallerTwice(config)).toEqual(runInstallerOnce(config))
```

- [ ] **Step 2: Run tests and confirm they fail**

Run: `docker run --rm -v "$PWD:/repo" -w /repo oven/bun:1.3.6 bun test integrations/coding-agents`

- [ ] **Step 3: Implement shared repository scoping and client configuration**

Configure Claude Code and Codex to use the same `/mcp` endpoint and bearer
environment variable. Install upstream-compatible hooks for automatic profile
and recall injection, and inject the resolved repository container so manual
MCP calls search both personal and project memory.

- [ ] **Step 4: Run tests and dry-run installers against temporary homes**

Run: `docker run --rm -v "$PWD:/repo" -w /repo oven/bun:1.3.6 bun test integrations/coding-agents`

- [ ] **Step 5: Commit**

```bash
git add integrations/coding-agents
git commit -m "feat(integrations): configure Claude and Codex memory"
```

### Task 8: Live backup, import, cutover, and remote history replacement

**Files:**
- Create outside Git: private raw exports, PostgreSQL dump, import plan, import journal, and checksum manifest.
- Modify remote: replace `olhapi/ram0` main history only after live verification.

**Interfaces:**
- Consumes all prior tasks.
- Produces a verified live Supermemory service reachable through the existing Cloudflare origins.

- [ ] **Step 1: Export both accounts and verify the source backup**

Confirm exactly two source accounts, validate JSON structure, list the custom
PostgreSQL dump with `pg_restore --list`, compute checksums, and record per-account
and per-scope counts without printing private memory content.

- [ ] **Step 2: Build the real import plan and review only aggregate statistics**

Run the planner with an explicit `app_id` mapping and confirm:

```text
source = imported_candidates + exact_duplicates + invalid
invalid = 0
unmapped_projects = 0
```

- [ ] **Step 3: Start the candidate and import all records**

Wait for Supermemory processing to settle, then verify imported count, profiles,
personal recall, repository recall, and one contradiction/update chain without
deleting or modifying the legacy source.

- [ ] **Step 4: Deploy to the existing Unraid ports and verify Cloudflare**

Verify unauthenticated rejection, authenticated REST search, MCP initialize and
tool invocation, graph explorer response, and both public Cloudflare origins.

- [ ] **Step 5: Replace remote history with lease protection**

Change remotes so `origin` is `https://github.com/olhapi/ram0.git` and `upstream`
is `https://github.com/supermemoryai/supermemory.git`. Fetch the existing remote
tip, then use `--force-with-lease` against that exact tip only after the deployed
revision passes every gate.

- [ ] **Step 6: Preserve the old checkout locally and verify final state**

Keep the Mem0 Git bundle and the old PostgreSQL/export artifacts locally. Verify
the new checkout is clean, `main` is based on Supermemory upstream, Unraid runs
the expected immutable images, and no old branch or data backup was pushed.
