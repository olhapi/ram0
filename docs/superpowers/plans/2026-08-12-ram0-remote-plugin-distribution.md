<!-- SPDX-FileCopyrightText: 2026 Ram0 contributors -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Ram0 Remote Plugin Distribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship Ram0 through a small generated Git marketplace so Codex and Claude Code can install and upgrade it natively without a local monorepo checkout.

**Architecture:** The Ram0 monorepo remains canonical. A deterministic Python exporter creates a strict allowlisted marketplace tree, while the plugin starts MCP from its bundled adapter and idempotently bootstraps the user CLI from that same reviewed bundle. A separately authorized publish task creates and updates `olhapi/ram0-plugins`.

**Tech Stack:** Python 3.10+, pytest, JSON marketplace manifests, shell lifecycle hooks, Codex and Claude Code CLIs, GitHub CLI, GitHub Actions pinned to immutable action SHAs.

## Global Constraints

- Canonical sources remain under `integrations/ram0-plugin/`; generated distribution files are never independently authored.
- The exported repository contains only marketplace manifests, the plugin bundle, README, LICENSE/NOTICE, and a source manifest.
- Export must be deterministic, reject non-empty output, reject escaping symlinks, preserve executable modes, and scan for credentials/config/caches/databases.
- Full-plugin MCP startup must work before `ram0` exists on `PATH`.
- CLI bootstrap is idempotent, atomic, private, and never reads or alters `~/.config/ram0/config.json`.
- Normal install/update documentation contains no clone, pull, cache deletion, or remove/re-add cycle.
- `ram0 setup` remains the only normal credential-writing flow.
- Marketplace/plugin identity is exactly `ram0@ram0-plugins`; Ram0 guidance contains no `mem0-plugins`.
- No new dependencies.
- Any third-party CI action is pinned to an immutable commit SHA.
- External repository creation/push occurs only in the final publishing task after local review and explicit authorization.
- Preserve all existing workflow-skill functionality and cross-client discovery.

---

### Task 1: Build the deterministic distribution exporter

**Files:**
- Create: `integrations/ram0-plugin/scripts/export_marketplace.py`
- Create: `integrations/ram0-plugin/tests/test_export_marketplace.py`
- Create: `integrations/ram0-plugin/distribution/README.md`
- Create: `integrations/ram0-plugin/distribution/LICENSE`
- Create: `integrations/ram0-plugin/distribution/NOTICE`

**Interfaces:**
- Produces: `export_marketplace(source_root: Path, output_root: Path, source_commit: str) -> dict[str, object]`.
- Output root contains root marketplace manifests, `plugins/ram0/`, documentation/license files, and `source-manifest.json`.

- [ ] **Step 1: Write failing exporter contract tests**

Test that an export to an empty temporary directory:
- contains exact allowlisted paths;
- includes all 12 `skills/*/SKILL.md`, hook manifests/entrypoints, MCP manifests, CLI/runtime scripts, and plugin manifests;
- excludes `.git`, `node_modules`, `__pycache__`, `.env`, `config.json`, databases, tests, server files, and unrelated integrations;
- preserves executable bits on shell hooks and `bin/ram0`;
- records `source_commit`, plugin version, and a sorted SHA-256 file manifest;
- yields byte-identical files and modes across two exports.

Add negative tests for non-empty output, missing required source, escaping symlink, version/name disagreement, and a seeded secret-shaped file.

- [ ] **Step 2: Run tests and confirm importer failure**

```bash
/Users/olhapi/Documents/ram0/.venv/bin/pytest integrations/ram0-plugin/tests/test_export_marketplace.py -q
```

Expected: import/file-not-found failure because the exporter does not exist.

- [ ] **Step 3: Implement the strict exporter**

Use only the Python standard library. Define explicit file/directory allowlists; never recursively copy the repository. Resolve each source and ensure it remains under the plugin root. Copy to an already-created empty output with `shutil.copy2`, normalize generated JSON with sorted keys and a trailing newline, scan destination names/content for forbidden material, then write the final source manifest.

The generated Codex and Claude marketplace manifests point to `./plugins/ram0`. The generated plugin version comes from the canonical plugin manifests and all host versions must match.

- [ ] **Step 4: Add a CLI**

```bash
python3 integrations/ram0-plugin/scripts/export_marketplace.py \
  --output /absolute/empty/output \
  --source-commit "$(git rev-parse HEAD)"
```

Require an absolute output path and print only a compact file-count/version/commit summary.

- [ ] **Step 5: Verify and commit**

Run focused tests, `python3 -m py_compile`, and `git diff --check`.

```bash
git add integrations/ram0-plugin/scripts/export_marketplace.py integrations/ram0-plugin/tests/test_export_marketplace.py integrations/ram0-plugin/distribution
git commit -m "feat(plugin): export lightweight Ram0 marketplace"
```

---

### Task 2: Bootstrap MCP and CLI from the active plugin bundle

**Files:**
- Modify: `integrations/ram0-plugin/.codex-mcp.json`
- Modify: `integrations/ram0-plugin/.mcp.json`
- Modify: `integrations/ram0-plugin/.cursor-mcp.json`
- Modify: `integrations/ram0-plugin/scripts/install_cli.py`
- Create: `integrations/ram0-plugin/scripts/bootstrap_cli.py`
- Modify: host session-start hook entrypoints/manifests under `integrations/ram0-plugin/hooks/` and `scripts/`
- Modify: `integrations/ram0-plugin/tests/test_hooks.py`
- Modify: `integrations/ram0-plugin/tests/test_ram0_cli.py`
- Modify: `integrations/ram0-plugin/tests/test_mcp_stdio_adapter.py`

**Interfaces:**
- Produces: `bootstrap(source_dir: Path, home: Path, stdout: TextIO, stderr: TextIO) -> bool`.
- MCP manifests execute the bundled adapter through host-supported plugin-root expansion, not `ram0 mcp`.

- [ ] **Step 1: Add failing first-start tests**

Add isolated-home tests proving:
- MCP initialization succeeds when `PATH` has no `ram0`;
- bootstrap installs exactly the four bounded runtime files;
- identical files are not rewritten;
- changed bundled files atomically replace runtime copies;
- existing config bytes, mode, inode target, and modification time are untouched;
- unsafe destination symlinks or permissions fail safely;
- hook startup continues when bootstrap fails and emits one actionable redacted diagnostic.

- [ ] **Step 2: Verify red tests**

Run the named hook/CLI/MCP tests and confirm failures reference the current `ram0 mcp` dependency and missing bootstrap.

- [ ] **Step 3: Implement atomic idempotent bootstrap**

Refactor bounded copy logic so manual `install_cli.py` and automatic `bootstrap_cli.py` share one implementation. Use private same-directory temporary files, `fsync`, `os.replace`, modes `0755` for the launcher and `0600` for runtime Python, and strict regular-file/directory checks. Never open the config file.

- [ ] **Step 4: Wire lifecycle bootstrap and bundled MCP**

Run bootstrap at the start of trusted session-start hooks before other plugin work; failure is non-fatal. Configure each host manifest using the exact verified plugin-root mechanism. For Codex and Claude, exercise actual isolated CLI startup rather than relying only on manifest-string tests.

- [ ] **Step 5: Verify and commit**

```bash
/Users/olhapi/Documents/ram0/.venv/bin/pytest integrations/ram0-plugin/tests -q
git diff --check
git add integrations/ram0-plugin
git commit -m "feat(plugin): bootstrap Ram0 CLI from plugin bundle"
```

---

### Task 3: Prove native remote install and update behavior

**Files:**
- Create: `integrations/ram0-plugin/tests/test_remote_marketplace.py`
- Modify: `integrations/ram0-plugin/scripts/export_marketplace.py`

**Interfaces:**
- Consumes: exported marketplace fixtures from Task 1 and bootstrap behavior from Task 2.
- Produces: controlled v1/v2 local Git marketplace fixtures that exercise native host update commands without external network state.

- [ ] **Step 1: Write failing Codex and Claude lifecycle tests**

Build two tiny Git repositories from exporter output:
- v1 contains a unique harmless version marker;
- v2 changes that marker and bundled CLI content.

For Codex, use a fresh `CODEX_HOME`, add the Git repository URL/path as a Git marketplace, install `ram0@ram0-plugins`, verify v1, publish v2 to the fixture origin, run `codex plugin marketplace upgrade ram0-plugins`, and verify whether the installed cache becomes v2. If Codex requires a supported additional native command, discover and encode it; do not remove/re-add.

For Claude, use an isolated config home, install v1, publish v2, run:
`claude plugin marketplace update ram0-plugins` and
`claude plugin update ram0@ram0-plugins`, then verify v2.

Skip with a precise reason only when a host CLI is unavailable.

- [ ] **Step 2: Verify tests fail against current distribution behavior**

Expected: missing exporter/update fixture support or stale installed marker.

- [ ] **Step 3: Make exporter/manifests update-safe**

Adjust only generated metadata needed for both hosts to detect and copy new versions. Version changes must be explicit; do not depend on mutable cache deletion. Record the verified Codex command sequence as structured test data for documentation tests.

- [ ] **Step 4: Verify first-start and update**

Confirm both hosts can install from fresh isolated homes, initialize bundled MCP without preinstalled CLI, bootstrap CLI, preserve config across v1→v2, and discover all 12 skills.

- [ ] **Step 5: Commit**

```bash
git add integrations/ram0-plugin/tests/test_remote_marketplace.py integrations/ram0-plugin/scripts/export_marketplace.py
git commit -m "test(plugin): verify native marketplace upgrades"
```

---

### Task 4: Add reviewed publication automation

**Files:**
- Create: `integrations/ram0-plugin/scripts/publish_marketplace.py`
- Create: `integrations/ram0-plugin/tests/test_publish_marketplace.py`
- Modify: `.github/workflows/ram0-plugin-checks.yml`
- Create: `.github/workflows/ram0-plugin-marketplace-publish.yml`
- Modify: `.github/workflows/ci-gate.yml` only if required to include exporter validation

**Interfaces:**
- Produces: a dry-run/default publication command and an explicitly enabled push mode targeting `olhapi/ram0-plugins`.
- Push mode requires an explicit destination checkout, expected remote URL, expected branch, and source SHA.

- [ ] **Step 1: Add failing publication safety tests**

Test that default mode only exports/diffs; push mode refuses a dirty destination, wrong remote, wrong branch, non-fast-forward state, unexpected files, or unchanged source manifest. Test that it commits only generated changes with a Conventional Commit containing source SHA.

- [ ] **Step 2: Implement the local publication orchestrator**

Use subprocess argument arrays without a shell. Never create/delete repositories, configure credentials, force-push, or mutate the source checkout. Require `--publish` plus all validated destination arguments before commit/push.

- [ ] **Step 3: Add CI validation and manual publication workflow**

Ram0 checks run exporter determinism and bundle tests. The publication workflow is `workflow_dispatch` only, uses least permissions, checks out source and distribution repositories, runs validation, and pushes only the generated distribution branch. Pin every third-party action to an immutable full commit SHA.

- [ ] **Step 4: Run workflow/static tests**

Validate YAML, action SHA pinning, exporter tests, publish tests, and existing CI routing expectations. Do not push externally in this task.

- [ ] **Step 5: Commit**

```bash
git add integrations/ram0-plugin/scripts/publish_marketplace.py integrations/ram0-plugin/tests/test_publish_marketplace.py .github/workflows/ram0-plugin-checks.yml .github/workflows/ram0-plugin-marketplace-publish.yml .github/workflows/ci-gate.yml
git commit -m "ci(plugin): publish generated Ram0 marketplace"
```

---

### Task 5: Replace normal-user checkout documentation

**Files:**
- Modify: `integrations/ram0-plugin/README.md`
- Modify: `docs/integrations/ram0-plugin.mdx`
- Modify: `docs/open-source/ram0-mcp.mdx`
- Modify: `integrations/ram0-plugin/tests/test_workflow_skills.py`
- Create: `integrations/ram0-plugin/tests/test_distribution_docs.py`
- Modify: `docs/llms.txt` only if the coverage checker requires it

**Interfaces:**
- Consumes: verified command sequences from Task 3.
- Produces: consistent first install, setup, update, verification, migration, and development sections.

- [ ] **Step 1: Add failing documentation contracts**

Assert every help page:
- uses `https://github.com/olhapi/ram0-plugins.git`;
- gives verified Codex and Claude install/update commands;
- names `ram0-plugins` consistently;
- states config preservation;
- includes verification and local-marketplace migration;
- contains clone/pull/install_cli only under a development heading;
- contains no normal-user remove/re-add, cache deletion, or `mem0-plugins`.

- [ ] **Step 2: Rewrite help pages**

Lead with remote installation. Separate:
1. Install marketplace/plugin.
2. Restart/trust hooks if required.
3. Run `ram0 setup`.
4. Run `ram0 config test`.
5. Verify MCP and skills.
6. Update through native commands.
7. Migrate old local marketplace.
8. Development-only checkout workflow.

Use only Task 3's verified command sequence.

- [ ] **Step 3: Run docs and plugin checks**

```bash
/Users/olhapi/Documents/ram0/.venv/bin/pytest integrations/ram0-plugin/tests -q
/Users/olhapi/Documents/ram0/.venv/bin/python scripts/check-llms-txt-coverage.py
git diff --check
```

- [ ] **Step 4: Commit**

```bash
git add integrations/ram0-plugin/README.md docs/integrations/ram0-plugin.mdx docs/open-source/ram0-mcp.mdx integrations/ram0-plugin/tests/test_distribution_docs.py integrations/ram0-plugin/tests/test_workflow_skills.py docs/llms.txt
git commit -m "docs(plugin): document native Ram0 plugin upgrades"
```

---

### Task 6: Create and publish the dedicated marketplace repository

**External target:** `github.com/olhapi/ram0-plugins`

**Interfaces:**
- Consumes: reviewed exporter and publisher, current feature branch source SHA.
- Produces: public protected distribution repository and verified clean-host install/update evidence.

- [ ] **Step 1: Run final local verification**

Run the full Ram0 plugin suite, OpenCode tests/typecheck/build, exporter determinism, publication dry-run, docs coverage, and `git diff --check`. Request final whole-branch code review and fix all Critical/Important findings.

- [ ] **Step 2: Request explicit external publication authorization**

Present:
- exact repository name and visibility;
- files to publish;
- source SHA;
- branch;
- whether GitHub Actions workflow/secrets are required; and
- the commands/API mutations to be performed.

Do not infer authorization from approval of this plan.

- [ ] **Step 3: Create the public repository**

After approval, use GitHub tooling to create `olhapi/ram0-plugins` without initializing unrelated content. Configure description/default branch. Push only validated generated output.

- [ ] **Step 4: Protect and verify**

Configure branch protection where permissions allow. Clone/add the public marketplace into fresh isolated Codex and Claude homes, install, start MCP, verify all 12 skills and CLI bootstrap, publish a controlled version increment, and verify native update commands reach the increment without local Git management.

- [ ] **Step 5: Publish source and distribution together**

Push the Ram0 feature branch/create PR or merge as explicitly chosen, then publish the generated distribution from the exact merged source SHA. Verify source manifest parity and public help URLs.

- [ ] **Step 6: Migrate this Mac only after approval**

Replace the local marketplace registration with the remote one using host commands, preserve `~/.config/ram0/config.json`, restart, and prove `ram0 config test`, MCP initialization, and skill discovery.
