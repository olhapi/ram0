# Dashboard Help Public Marketplace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make dashboard Help a public-marketplace-only consumer guide with accurate Mem0 link labeling.

**Architecture:** Keep installation strings in `help-content.ts` and rendering in `page.tsx`. Replace checkout-dependent fields with public install/update fields, render those consistently for supported clients, and lock the contract with Node tests.

**Tech Stack:** Next.js, TypeScript, Node test runner, pnpm.

## Global Constraints

- Help contains no repository clone, local checkout, contributor, source-build, or local OpenCode build instructions.
- The public marketplace is `https://github.com/olhapi/ram0-plugins.git`.
- The section title remains `Using Ram0`.
- A `docs.mem0.ai` link must identify its destination as Mem0.
- Persistent configuration remains secret-safe in `~/.config/ram0/config.json`.

---

### Task 1: Public marketplace Help contract

**Files:**
- Modify: `server/dashboard/src/app/(root)/dashboard/help/help-content.test.ts`
- Modify: `server/dashboard/src/app/(root)/dashboard/help/help-content.ts`
- Modify: `server/dashboard/src/app/(root)/dashboard/help/page.tsx`

**Interfaces:**
- Consumes: `agentInstalls(apiUrl?: string): AgentInstall[]`
- Produces: per-client `pluginInstall` and `pluginUpdate` consumer instructions without checkout dependencies.

- [ ] **Step 1: Write failing contract tests**

Assert that Help content contains the public marketplace URL and Codex/Claude install and update commands; recursively reject `git clone`, `~/ram0-plugins`, contributor language, `bun install`, and `file://`; assert the page keeps `Using Ram0`, renders update instructions, labels the external link `Mem0 MCP guide`, and never labels a `docs.mem0.ai` destination as Ram0.

- [ ] **Step 2: Verify RED**

Run: `pnpm test -- help/help-content.test.ts`

Expected: FAIL because the current Help contract includes checkout-dependent commands and `Ram0 MCP guide`.

- [ ] **Step 3: Implement minimal consumer-only Help**

Use `https://github.com/olhapi/ram0-plugins.git` for Codex and Claude marketplace installation, add their native update commands, remove CLI installation by source checkout, and replace unsupported Cursor/OpenCode source-build instructions with concise public-distribution availability guidance. Preserve persistent setup, direct MCP, migration, and troubleshooting. Render `Mem0 MCP guide` for the existing `docs.mem0.ai` link.

- [ ] **Step 4: Verify GREEN and package quality**

Run:

```bash
pnpm test
pnpm run lint
pnpm run build
```

Expected: all commands exit 0.

- [ ] **Step 5: Commit**

```bash
git add server/dashboard/src/app/\(root\)/dashboard/help
git commit -m "fix(dashboard): use public plugin marketplace in help"
```

### Task 2: Publish and deploy

**Files:**
- No source files beyond Task 1.

**Interfaces:**
- Consumes: verified branch commit and immutable GHCR dashboard image.
- Produces: merged `main` revision deployed by `server/scripts/deploy_unraid.sh`.

- [ ] **Step 1: Push and merge the reviewed hotfix**

Push `fix/help-marketplace-copy`, open a PR, require the repository CI gate, and merge only after it passes.

- [ ] **Step 2: Verify immutable images**

Confirm the GHCR workflow succeeds for the exact merged `main` SHA.

- [ ] **Step 3: Deploy through the guarded Unraid workflow**

On `root@192.168.1.2`, fast-forward the clean tracked checkout while preserving host-owned untracked overrides, then run:

```bash
server/scripts/deploy_unraid.sh <40-character-merged-main-sha>
```

- [ ] **Step 4: Verify production**

Confirm `current.env`, API/dashboard image revision labels, database revision `008 (head)`, direct endpoints, public endpoints, and deployed Help content all match the merged SHA and new contract.
