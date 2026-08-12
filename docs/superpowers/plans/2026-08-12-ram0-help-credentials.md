# Ram0 Help Credential Setup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the dashboard Help page show every supported agent exactly how to place its Ram0 credentials before connecting MCP or automation.

**Architecture:** Extend the existing `AgentInstall` presentation model with client-specific credential setup, verification, and persistence notes. Render that shared data first in both the direct-MCP and full-automation agent-tab flows, keeping API keys as runtime prompt input only and never as dashboard data.

**Tech Stack:** Next.js 15, React 19, TypeScript, Node built-in test runner, Prettier, TypeScript compiler.

## Global Constraints

- Never interpolate, store, or display an API-key literal in dashboard source, copied commands, or tests.
- The configured URL must remain validated by `normalizedApiUrl` and generated MCP/plugin commands must continue to reference `RAM0_API_KEY` only.
- Codex Desktop guidance uses `read -rs` and `launchctl setenv`; it states that credentials reset at logout/reboot and requires a full restart plus hook trust.
- Preserve the four supported tabs: Codex, Claude Code, Cursor, and OpenCode.

---

### Task 1: Add credential setup data and test coverage

**Files:**

- Modify: `server/dashboard/src/app/(root)/dashboard/help/help-content.ts`
- Modify: `server/dashboard/src/app/(root)/dashboard/help/help-content.test.ts`

**Interfaces:**

- Produces: `AgentInstall.credentialSetup: string`, `AgentInstall.credentialNote: string`, and `AgentInstall.credentialVerify: string` for all four agent records.
- Consumes: `normalizedApiUrl(apiUrl?: string): string | null` and `agentInstalls(apiUrl?: string): AgentInstall[]`.

- [ ] **Step 1: Write the failing test**

Add a test that asserts all four records expose `credentialSetup`, `credentialNote`, and `credentialVerify`; the setup uses a hidden `read` prompt and no key literal. Assert Codex includes `launchctl setenv RAM0_API_KEY`, logout/reboot persistence guidance, and `/hooks` trust guidance.

- [ ] **Step 2: Run test to verify it fails**

Run `cd server/dashboard && node --experimental-strip-types --test src/app/\(root\)/dashboard/help/help-content.test.ts`.

Expected: FAIL because `AgentInstall` has no credential properties.

- [ ] **Step 3: Add the minimal credential model and records**

Add the three string fields to `AgentInstall`. Each agent provides a safe setup command, safe verification command, and client-specific persistence/restart note. Codex uses a `read -rs` prompt, then `launchctl setenv RAM0_API_URL` and `launchctl setenv RAM0_API_KEY`, followed by `unset RAM0_API_KEY`.

- [ ] **Step 4: Run test to verify it passes**

Run `cd server/dashboard && node --experimental-strip-types --test src/app/\(root\)/dashboard/help/help-content.test.ts`.

Expected: PASS, including all existing Help tests.

- [ ] **Step 5: Commit**

Run `git add server/dashboard/src/app/\(root\)/dashboard/help/help-content.ts server/dashboard/src/app/\(root\)/dashboard/help/help-content.test.ts && git commit -m "feat(help): explain Ram0 credential setup"`.

### Task 2: Render credentials before connection commands

**Files:**

- Modify: `server/dashboard/src/app/(root)/dashboard/help/page.tsx`
- Modify: `server/dashboard/src/app/(root)/dashboard/help/help-content.test.ts`

**Interfaces:**

- Consumes: the three credential fields added to `AgentInstall` in Task 1.
- Produces: a credential setup panel shown first in each agent tab, before direct MCP and automation commands.

- [ ] **Step 1: Write the failing test**

Add assertions that the page renders `Set Ram0 credentials`, `install.credentialSetup`, `install.credentialVerify`, and an explicit warning not to paste API keys into MCP configuration or the dashboard.

- [ ] **Step 2: Run test to verify it fails**

Run `cd server/dashboard && node --experimental-strip-types --test src/app/\(root\)/dashboard/help/help-content.test.ts`.

Expected: FAIL because the page does not render credential setup fields.

- [ ] **Step 3: Render the shared credential panel**

In each agent tab, place a `Set Ram0 credentials` panel before its direct MCP command. Include a copy button for `install.credentialSetup`, the persistence/restart note, and `install.credentialVerify`. State: `Do not paste your API key into MCP JSON, plugin manifests, source code, or this dashboard.` Avoid duplicating the panel in automation; point it back to the first step.

- [ ] **Step 4: Run focused tests and dashboard validation**

Run `cd server/dashboard && node --experimental-strip-types --test src/app/\(root\)/dashboard/help/help-content.test.ts && pnpm lint && pnpm typecheck && pnpm build`.

Expected: all Help tests, formatting, typecheck, and production build pass.

- [ ] **Step 5: Commit**

Run `git add server/dashboard/src/app/\(root\)/dashboard/help/page.tsx server/dashboard/src/app/\(root\)/dashboard/help/help-content.test.ts && git commit -m "feat(help): show Ram0 credential placement"`.
