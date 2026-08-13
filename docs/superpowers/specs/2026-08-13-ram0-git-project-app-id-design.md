<!-- SPDX-FileCopyrightText: 2026 Ram0 contributors -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- Modified for Ram0; see NOTICE and repository history. -->

# Git Project Memory Scope via `app_id`

**Date:** 2026-08-13
**Status:** Approved design

## Goal

Add Git-project-aware memory organization to Ram0 using Mem0 Platform's existing `app_id` convention, without adding project records, organization membership, project settings, or a project-management UI.

Normal recall should combine durable account-wide knowledge with knowledge for the current Git repository. Project-specific memories must not bleed into other repositories, and the authenticated account must remain the only security boundary.

## Terminology

- **Owner:** The authenticated Ram0 account UUID. Ram0 derives it server-side and uses it as the mandatory authorization boundary.
- **App ID:** An account-local string stored as `app_id`, normally derived from the current Git repository. It organizes memories but does not grant access.
- **Project memory:** A memory whose `app_id` is the current repository's resolved app ID.
- **Global memory:** A memory with no `app_id`.
- **Scope:** A read or write policy controlling how `app_id` is applied. It never changes the owner.

The user-facing term may be **project** or **workspace**, while the REST and storage compatibility field remains `app_id`.

## Non-goals

- Mem0 Platform organization or project APIs
- Project tables, UUIDs, members, invitations, roles, settings, or ownership transfer
- Per-project category catalogs or extraction instructions
- Treating `app_id` as an authentication or authorization boundary
- Automatically classifying captured content as global
- Cross-account project sharing

## Chosen Approach

Implement native Ram0 `app_id` compatibility across the existing REST, MCP, plugin, dashboard, and verification seams.

Rejected alternatives:

- **Metadata-only project tags:** Smaller initially, but incompatible with Mem0's field shape and easier for individual call sites to omit or mishandle.
- **Map projects to `agent_id`:** Avoids an OSS extension but destroys the meaning of agent identity and blocks clean future agent scoping.
- **First-class projects subsystem:** Adds lifecycle and authorization complexity that the requested Git-project grouping does not need.

## Invariants

Every memory operation must obey:

```text
authenticated account
  -> canonical account UUID
    -> optional app_id organization filter
      -> memory operation
```

Specifically:

1. Ram0 always derives `user_id` from the authenticated account.
2. `app_id` can only narrow or organize that account's memories; it can never broaden access.
3. The same `app_id` used by two accounts represents two independent account-local groups.
4. Missing and foreign memory IDs remain indistinguishable.
5. Direct UUID CRUD remains authorized by owner, independent of the current project context.
6. Bulk operations require explicit bounded scope and remain owner-filtered.

## Project Resolution

The local plugin resolves the current app ID because the remote MCP server cannot observe the agent's working directory.

Resolution order:

1. Explicit `RAM0_PROJECT_ID` override.
2. Saved local mapping keyed by working directory.
3. Saved local mapping keyed by a normalized Git remote identity, allowing moved or renamed clones to retain their mapping.
4. A deterministic identifier derived from the Git `origin` remote.
5. Git repository-root directory name.
6. Current working-directory name for non-Git directories.

The resolver should reuse the upstream Mem0 plugin design where practical, while fixing obvious identity weaknesses:

- Normalize equivalent SSH and HTTPS forms of a remote.
- Include the Git host in the durable remote identity so repositories with the same owner/name on different hosts do not collide.
- Avoid storing full local paths or remote URLs in memories.
- Keep local aliases and mappings in protected local plugin state rather than the Ram0 server.
- Resolve every Git worktree for the same remote to the same app ID unless explicitly overridden.

The resolver must fail safely. If no non-empty project identifier can be produced, project-scoped writes fail without storing an ambiguously scoped memory; callers may explicitly choose a global write.

## Scope Semantics

### Reads

| Scope | Result set |
|---|---|
| Omitted/default | Current `app_id` plus global memories with no `app_id` |
| `project` | Current `app_id` only |
| `global` | All memories owned by the authenticated account |

Default read filtering is conceptually:

```text
owner_id = authenticated account
AND (app_id = current app_id OR app_id is absent)
```

It must exclude memories assigned to every other `app_id`.

### Writes

| Scope | Stored app scope |
|---|---|
| Omitted/default | Current `app_id` |
| `project` | Current `app_id` |
| `global` | No `app_id` |

Automatic lifecycle capture always writes to the current project. An explicit global scope is required to create an account-wide memory. Ram0 will not initially infer global versus project scope from memory content.

### Existing memories

Existing memories have no `app_id`; they therefore become global without a data rewrite. They appear in default and global recall, but not project-only recall.

## REST and Storage Contract

Ram0 adds `app_id` as an optional top-level memory field compatible with hosted Mem0's naming:

- Create accepts top-level `app_id` where the caller has a legitimate project context.
- Search and list accept `app_id` through structured filters and through any retained compatibility parameters.
- Memory output includes `app_id` when present.
- Delete-all can narrow by `app_id`, but Ram0 always adds the authenticated owner.
- Entity listing and deletion add the `app` entity type alongside `agent` and `run`.

`app_id` is not accepted inside arbitrary metadata. Reserved-field validation prevents metadata from shadowing `user_id`, `app_id`, category state, expiration state, or other server-owned controls.

The underlying vector-store payload carries `app_id`. All supported backup, restore, migration, history, category processing, and job paths must preserve it without treating it as owner identity.

## MCP Contract

Keep the existing six MCP tools. Extend the tools that need contextual scope:

```text
remember(content, metadata?, scope?, app_id?)
search_memories(query, limit?, scope?, app_id?)
list_memories(limit?, scope?, app_id?)
```

Lifecycle hooks resolve cwd per event and the plugin's automatic lifecycle client calls apply that resolved `app_id`; `scope` communicates the intended policy. Standard interactive MCP calls have no trustworthy per-call cwd, so the hook context is advisory and the agent supplies the validated `app_id`. Requests without a usable project ID may perform explicit global reads or writes, but must not silently turn a default project write into a global write.

`get_memory`, `update_memory`, and `forget_memory` continue taking a memory UUID and enforcing authenticated ownership. They do not require the current scope.

`scope` accepts only `project` or `global`; omission selects the default behavior. `app_id` is required for omitted/default reads, omitted/default writes, and explicit project scope. It is rejected for explicit global writes because those must remain unscoped. Automatic lifecycle calls use their per-event resolved ID; interactive plugin and direct MCP callers provide the intended validated ID explicitly because MCP has no per-call cwd. `app_id` is grouping, not ownership, while `user_id` remains unavailable to every MCP caller.

## Plugin and Hook Behavior

The Ram0 plugin should adopt the relevant upstream Mem0 lifecycle behavior:

- Resolve project context at session start and when the working directory changes.
- Export the resolved project identifier to hooks without exposing credentials.
- Inject the current `app_id` into default and project-scoped writes.
- Construct default reads as current-project plus global.
- Construct project reads as current-project only.
- Construct global reads as all memories for the authenticated account.
- For automatic lifecycle calls, supply the per-event resolved `app_id`. For interactive MCP, present that context as advisory and allow the agent to select any validated account-local `app_id`.
- Store automatic prompt, stop, and compaction captures in the current project.

Direct MCP registration without the plugin remains usable, but it has no automatic Git detection. Its caller must explicitly provide validated project context or choose global scope.

## Dashboard

The first release adds visibility, not management:

- Show `app_id` in memory rows/details when present.
- List `app` entities with memory counts and timestamps alongside agent/run entities.
- Allow bounded deletion of an app entity using the existing confirmation behavior.
- Describe absent `app_id` as global/unscoped.

Do not add create, rename, membership, settings, or project-administration screens.

## Validation and Error Handling

- Reject empty, overlong, malformed, or structurally unsafe `app_id` values with a stable validation error.
- Define and document a conservative length and character contract that supports normalized Git identifiers and explicit overrides.
- Reject reserved identity keys embedded in metadata.
- If default/project scope lacks current project context, return a clear scope error instead of falling back to global.
- Unknown scope values return an invalid-argument response.
- Search and list must never degrade to owner-wide global recall because an `app_id` filter was malformed or missing.
- Provider and internal errors retain the existing redacted error contract.

## Verification Strategy

### Resolver tests

- Explicit override precedence
- Saved path mapping
- Saved normalized-remote mapping after a checkout move
- Equivalent SSH/HTTPS remote normalization
- Git-host collision avoidance
- Ordinary clone and Git worktree equivalence
- Repository-root fallback without an origin
- Non-Git working-directory fallback
- Empty/invalid context failure

### Scope contract tests

For one account containing global, project A, and project B memories:

- Default read returns global plus project A.
- Project read returns only project A.
- Global read returns all three groups.
- Default/project writes store project A.
- Global writes omit `app_id`.
- Existing unscoped memories remain available through default recall.

### Authorization tests

- Two accounts using the same `app_id` remain isolated.
- Supplying another account's app label never reveals its memories.
- Foreign direct-memory IDs return the same not-found response as missing IDs.
- App entity listing/deletion sees and mutates only the authenticated account.
- Every bulk/reset/category/job/reclassification path retains owner filtering.

### End-to-end tests

- REST add/search/list/update/delete preserve `app_id` correctly.
- MCP default, project, and global scopes match the contract.
- Plugin hooks resolve Git project context per event and scope automatic lifecycle calls without sending `user_id` or secrets; interactive MCP receives advisory context and remains agent-selectable.
- Dashboard renders global and project memories accurately.
- Backup/restore and ownership migration preserve app-scoped payloads.
- The disposable PostgreSQL/pgvector real-stack verifier proves cross-account and cross-project isolation.

Run focused tests first, then the server, plugin, dashboard, and real-stack suites required by the touched packages. Keep unrelated upstream baseline failures distinct from feature regressions.

## Compatibility and Rollout

- No rewrite is required for existing memories.
- Existing clients that omit `app_id` continue creating global memories through REST, subject to their existing authorization contract.
- The plugin changes its default writes to project-scoped and its default reads to project-plus-global.
- The feature should be delivered as a narrow Ram0 adapter/server extension so upstream Mem0 changes remain mergeable.
- Deployment must verify storage, API, MCP, plugin, dashboard, and backup behavior before claiming the feature active.

## Success Criteria

The feature is complete when an agent working in one Git repository automatically recalls that repository's memories plus global account memories, cannot recall another repository's project memories without an explicit global search, stores normal captured learnings under the current repository, and cannot cross the authenticated account boundary under any app ID or scope combination.
