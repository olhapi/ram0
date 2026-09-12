# Ram0 to Supermemory Local Cutover Design

## Objective

Replace the Mem0-derived Ram0 service with a Supermemory-based, single-user
deployment while preserving every existing memory and the current Unraid and
Cloudflare network contract. Claude Code and Codex must receive automatic
memory context and must also be able to recall and manage memory on demand via
MCP.

## Constraints

- The Supermemory self-hosted graph engine is distributed as a binary; its
  source is not present in the public monorepo. Pin the binary version and
  SHA-256 and treat it as a replaceable runtime dependency.
- Preserve the complete pre-cutover Git repository and live Ram0 PostgreSQL
  data locally. Never delete or overwrite the old database during migration.
- Keep the existing Unraid host bindings: API/MCP on port `18888` and the web
  interface on port `13000`.
- Keep the existing Cloudflare tunnel routes unchanged by preserving their
  local origins.
- The deployment serves one human. The two existing Ram0 accounts merge into
  one Supermemory identity.
- Secrets remain outside Git in the root-owned Unraid environment file.

## Repository and Upstream History

The replacement branch starts directly from `supermemoryai/supermemory/main`.
Ram0-owned changes are a small series of Conventional Commits on top, so future
upstream synchronization remains a normal merge or rebase rather than a
cross-project port.

Before remote history changes:

1. Create and verify a complete Git bundle of the current Mem0-derived repo.
2. Export both live accounts through the API or directly from the protected
   PostgreSQL container.
3. Create and verify a custom-format PostgreSQL dump.
4. Record SHA-256 checksums, counts, and source-to-target mappings.

The old Git bundle, PostgreSQL dump, raw JSON exports, and old Compose inputs
remain local only.

## Runtime Architecture

```text
Cloudflare tunnel
    |-- existing API origin :18888
    |       `-- local gateway
    |             |-- /mcp -> native Streamable HTTP MCP server
    |             `-- /*   -> Supermemory Local REST API
    |
    `-- existing dashboard origin :13000
            `-- Supermemory memory-graph explorer

local gateway --> supermemory-server:6767
                       `-- /data (new persistent graph directory)
```

The gateway owns only transport concerns: bearer-key enforcement for MCP,
request limits, MCP tool definitions, health aggregation, and reverse proxying.
Memory extraction, graph evolution, profiles, search, and persistence remain
inside the pinned Supermemory engine.

The graph explorer is the upstream `memory-graph-playground` configured to use
the internal local API rather than the hosted platform. It asks for the API key
in the browser and does not persist it server-side.

## Memory Topology

- `personal`: identity, preferences, cross-project workflows, and other global
  knowledge.
- `repo_<name>__<remote-hash>`: project-specific decisions, architecture,
  constraints, bugs, and implementation lessons.

Automatic hooks search both `personal` and the current repository container.
MCP recall does the same when a repository container is supplied; an explicit
container override searches only that container. Results are deduplicated by
memory identity/content and reranked by similarity.

Repository identity follows the official coding-plugin convention: normalized
Git remote when present, with the Git common directory as the local fallback.
Claude Code and Codex therefore share project memory across clones and linked
worktrees.

## MCP Surface

The local MCP service is Supermemory-native and intentionally drops Ram0/Mem0
compatibility. Model-visible operations are:

- `search_memory`: recall relevant memories, optionally including profiles,
  across `personal` and the current repository.
- `add_memory`: save or forget information in `personal`, the current
  repository, or an explicit container.
- `list_memories`: inspect extracted memory entries in one container.
- `list_documents`: inspect source documents in one container.
- `get_document`: retrieve one source document by ID.
- `list_spaces`: enumerate available container tags.
- `who_am_i`: report the local single-user server and effective scope.

The server also exposes `supermemory://profile` and
`supermemory://spaces` resources and a `context` prompt. Destructive forgetting
requires an explicit action and returns the affected memory identifier or a
clear no-match result.

MCP uses stateless Streamable HTTP at `/mcp`. A valid bearer API key is required
on every request. The backend key is never embedded in images, source, logs, or
MCP responses.

## Claude Code and Codex Integration

The integration retains two complementary paths:

1. Lifecycle hooks perform bounded automatic capture and inject profile plus
   relevant personal/project context without blocking the agent when memory is
   unavailable.
2. MCP lets the model search, inspect, save, and forget memory whenever the
   task requires more context than the automatic injection supplied.

Hook and MCP instructions include the resolved repository container tag. Raw
credentials, Git URLs containing credentials, complete transcripts, source
files, and diffs are not written as metadata.

## Migration

The migration has separate export, plan, import, and verify phases.

1. Quiesce automatic Ram0 writes for the final export window.
2. Export all memories from both Ram0 owner UUIDs, including expired records,
   original IDs, timestamps, `app_id`, categories, and safe metadata.
3. Normalize records into a deterministic JSONL import plan.
4. Exact-deduplicate identical content while retaining provenance for every
   source record. Do not semantically discard conflicts; allow Supermemory to
   model updates and contradictions.
5. Map records with no `app_id` to `personal`. Map project records through an
   explicit `app_id` to repository-container manifest.
6. Import existing atomic facts with the v4 memory endpoint so migration does
   not reinterpret them as raw conversation documents. Preserve original IDs
   and timestamps in namespaced migration metadata.
7. Wait for processing, compare planned/imported/failed counts, list memories,
   and run representative personal and project searches.

An import journal makes the operation resumable and prevents duplicate writes.
The old database remains the source of truth until every planned record is
either imported or reported with an actionable failure.

## Deployment and Rollback

The Unraid Compose stack uses three containers: the pinned engine image, the
local gateway, and the graph explorer. The engine data mounts at a new path
under `/mnt/user/appdata/mem0/data/supermemory`; the existing PostgreSQL and
history directories remain unchanged.

Cutover procedure:

1. Validate backups and import plan.
2. Build and test immutable images for the target Git revision.
3. Start the candidate engine on non-public verification ports and import the
   memories.
4. Exercise REST, MCP, profile, graph, and cross-container recall checks.
5. Stop the old application containers without removing volumes.
6. Bind the candidate gateway and graph explorer to `18888` and `13000`.
7. Verify direct Unraid health and both existing public Cloudflare origins.
8. Promote the deployment state only after all checks pass.

Rollback stops the candidate containers, restores the previous Compose inputs
and images, and restarts Ram0 against the untouched PostgreSQL directory. The
new Supermemory directory is retained for diagnosis rather than deleted.

## Verification Gates

- Git bundle verifies and its checksum is recorded.
- PostgreSQL dump lists successfully with `pg_restore --list`.
- Two source accounts are present in the export.
- Export count equals import-plan source count.
- Every source record maps to an imported record or an explicit failure.
- Duplicate consolidation retains all source IDs in provenance.
- Gateway unit and transport tests pass.
- MCP initialize, tool listing, `search_memory`, and `add_memory` work over the
  real Streamable HTTP transport.
- REST add/profile/search work against the pinned local engine.
- Graph explorer builds and reads the local API.
- Direct Unraid ports and existing Cloudflare URLs pass health and authenticated
  recall checks after cutover.
