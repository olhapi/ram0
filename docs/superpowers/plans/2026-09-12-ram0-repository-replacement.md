# Ram0 Repository Replacement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `olhapi/ram0` with a verified Supermemory-based fork under the same URL, publish Ram0-specific documentation, and satisfy the source licenses visible in the repository without publicly redistributing the ambiguously licensed engine binary.

**Architecture:** Prepare and test the public identity and license packaging on the existing Supermemory branch first. Back up every ref and available non-secret GitHub metadata, delete and recreate the repository as a fork of `supermemoryai/supermemory`, then fast-forward the fork with the Ram0 commits and verify it from a fresh clone.

**Tech Stack:** Git, GitHub CLI, Bash, Docker/Compose static checks, Bun/Vitest, Markdown, MIT and Apache-2.0 license files.

**Spec:** `docs/superpowers/specs/2026-09-12-ram0-repository-rebase-design.md`

## Global Constraints

- The public repository URL remains `https://github.com/olhapi/ram0`.
- The old Mem0 repository is deleted only after a complete local mirror, bundle, metadata export, and integrity checks pass.
- The local backup is retained after migration and must not be cleaned up.
- The recreated repository must be a GitHub fork of `supermemoryai/supermemory`.
- The upstream root `LICENSE` remains unchanged; nested component licenses remain in place.
- Ram0 is described as an unofficial fork and does not use upstream logos as its public identity.
- Images containing `supermemory-server` remain private; no release mirrors the binary.
- No passwords, API keys, tokens, secret values, or secret-bearing files are printed or committed.
- No Mem0 branches or Mem0 tags are pushed into the recreated Supermemory fork.
- The deployed data and containers are not changed by this repository-only migration.

---

### Task 1: Add public-source compliance tests

**Files:**
- Create: `deploy/supermemory/tests/test-public-source-compliance.sh`
- Modify: `deploy/supermemory/tests/verify-compose.sh`

**Interfaces:**
- Consumes: root `README.md`, `LICENSE`, `NOTICE`, and the three runtime Dockerfiles.
- Produces: a deterministic shell gate that fails when attribution, redistribution restrictions, or runtime license copies disappear.

- [ ] **Step 1: Write the failing compliance test**

Create `deploy/supermemory/tests/test-public-source-compliance.sh`:

```bash
#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Ram0 contributors
# SPDX-License-Identifier: MIT

set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)

grep -Fq '# Ram0' "$ROOT/README.md"
grep -Fiq 'unofficial' "$ROOT/README.md"
grep -Fq 'supermemoryai/supermemory' "$ROOT/README.md"
grep -Fiq 'must remain private' "$ROOT/README.md"
grep -Fq 'Copyright (c) 2025 supermemory' "$ROOT/LICENSE"
grep -Fq 'It is not source code from this repository' "$ROOT/NOTICE"

if grep -Fq 'apps/web/public/logo-fullmark.svg' "$ROOT/README.md"; then
  echo 'README must not use the upstream logo as Ram0 branding' >&2
  exit 1
fi

for dockerfile in \
  "$ROOT/apps/local-gateway/Dockerfile" \
  "$ROOT/apps/memory-graph-playground/Dockerfile" \
  "$ROOT/deploy/supermemory/Dockerfile.engine"; do
  grep -Fq 'COPY LICENSE NOTICE /usr/share/licenses/ram0/' "$dockerfile"
done

echo 'public source compliance checks passed'
```

Add this call to `deploy/supermemory/tests/verify-compose.sh` immediately after the existing compose-security test:

```bash
bash "$ROOT/deploy/supermemory/tests/test-public-source-compliance.sh"
```

- [ ] **Step 2: Run the new test and verify the expected failure**

Run:

```bash
bash deploy/supermemory/tests/test-public-source-compliance.sh
```

Expected: FAIL because the current README is upstream-branded and the Dockerfiles do not copy `LICENSE` and `NOTICE` into their runtime stages.

- [ ] **Step 3: Commit the failing test**

```bash
git add deploy/supermemory/tests/test-public-source-compliance.sh deploy/supermemory/tests/verify-compose.sh
git commit -m "test: enforce Ram0 source compliance"
```

### Task 2: Replace the public README and strengthen the notice

**Files:**
- Modify: `README.md`
- Modify: `NOTICE`
- Test: `deploy/supermemory/tests/test-public-source-compliance.sh`

**Interfaces:**
- Consumes: existing deployment and integration documentation.
- Produces: the canonical public identity and safe distribution statement for Ram0.

- [ ] **Step 1: Replace `README.md` with the Ram0 landing page**

Use this structure and content, retaining the commands exactly:

```markdown
# Ram0

Ram0 is an unofficial, self-hosted fork derived from
[Supermemory](https://github.com/supermemoryai/supermemory). It adds an
authenticated HTTP/MCP gateway, a self-hosted graph UI, Claude Code and Codex
integrations, guarded Unraid deployment, and resumable Mem0 migration tooling.

Ram0 is independently maintained and is not sponsored, endorsed, or supported
by Supermemory.

## What this fork adds

- Streamable HTTP MCP tools for explicit recall, capture, listing, and inspection.
- Automatic recall and capture hooks for Claude Code and Codex.
- Stable repository-scoped memory shared across clones and linked worktrees.
- A bearer-authenticated compatibility gateway for the local Supermemory engine.
- A graph interface that reads the self-hosted API rather than the hosted service.
- Resumable, count-verified migration from two Ram0/Mem0 user accounts.
- A guarded Unraid cutover that preserves the legacy database and ports.

## Architecture

`Claude Code / Codex -> HTTPS gateway -> local Supermemory engine`

The gateway also exposes `/mcp`; the graph application uses the same gateway.
The default deployment binds the API to port `18888` and the graph application
to port `13000`. Credentials are provided at runtime and never belong in Git.

## Deploy

Read [`deploy/supermemory/README.md`](deploy/supermemory/README.md). The deployment
script stages candidate containers, verifies them, backs up the legacy database,
and promotes only after readiness checks pass.

## Install Claude Code and Codex integrations

Requirements: Node.js 20+, current Claude Code, current Codex, and the gateway
key in `SUPERMEMORY_API_KEY`.

```bash
node integrations/coding-agents/install.mjs \
  --base-url https://brain-api.example.com \
  --force

source ~/.config/ram0-supermemory/env.sh
```

Keep the key in a mode-`0600` secret file or secret manager and source it before
the generated helper. Never place it in this repository or a command argument.
See [`integrations/coding-agents/README.md`](integrations/coding-agents/README.md)
for installation, project scoping, migration from the old plugin, and checks.

## Migrate Mem0 memories

The importer is resumable and verifies source-to-destination counts. Read
[`tools/ram0-migration/README.md`](tools/ram0-migration/README.md) before running
it. Preserve the source database and export until the destination audit passes.

## Upstream maintenance

`origin` is the Ram0 fork and `upstream` is
`https://github.com/supermemoryai/supermemory.git`. Upstream changes are brought
in through a dedicated sync branch, followed by tests, license review, and a
reviewed pull request. Ram0 release tags are immutable.

## Security and distribution

- The gateway requires bearer authentication for API and MCP requests.
- Runtime secrets and migration exports are excluded from Git.
- The legacy database is retained as a rollback source.
- The engine build downloads an official, version- and checksum-pinned
  `supermemory-server` release asset.
- The release asset does not currently state sufficiently explicit binary
  redistribution terms. Images containing it must remain private and the binary
  must not be attached to a public Ram0 release unless Supermemory supplies
  written license clarification.

## License and attribution

The upstream source is distributed under the root [MIT license](LICENSE), whose
copyright and permission notice are retained unchanged. Ram0 additions are also
MIT licensed. `skills/supermemory` is a separately licensed Apache-2.0 component
and retains its own license.

See [NOTICE](NOTICE) for provenance and
[`docs/research/2026-09-12-supermemory-fork-licensing.md`](docs/research/2026-09-12-supermemory-fork-licensing.md)
for the technical licensing review. Ram0 is not legal advice and does not claim
rights that an upstream dependency or release asset has not documented.
```

- [ ] **Step 2: Add the engine redistribution boundary to `NOTICE`**

Append:

```text

The Supermemory source tree is MIT licensed, but the downloaded engine release
asset does not publish sufficiently explicit asset-specific redistribution
terms. Ram0 does not assert a license for that binary. Images containing it are
kept private pending written clarification from Supermemory.
```

- [ ] **Step 3: Run the compliance test**

```bash
bash deploy/supermemory/tests/test-public-source-compliance.sh
```

Expected: still FAIL only on missing Dockerfile license copies.

- [ ] **Step 4: Commit the public identity**

```bash
git add README.md NOTICE
git commit -m "docs: establish Ram0 Supermemory identity"
```

### Task 3: Package notices in runtime images

**Files:**
- Modify: `apps/local-gateway/Dockerfile`
- Modify: `apps/memory-graph-playground/Dockerfile`
- Modify: `deploy/supermemory/Dockerfile.engine`
- Test: `deploy/supermemory/tests/test-public-source-compliance.sh`

**Interfaces:**
- Consumes: root `LICENSE` and `NOTICE` from each Docker build context.
- Produces: `/usr/share/licenses/ram0/LICENSE` and `/usr/share/licenses/ram0/NOTICE` in every Ram0 runtime image.

- [ ] **Step 1: Add license copies to every runtime stage**

Immediately after each runtime stage's `WORKDIR`, add:

```dockerfile
COPY LICENSE NOTICE /usr/share/licenses/ram0/
```

Do this in all three Dockerfiles listed above. Do not label the engine image as
wholly MIT licensed, because the downloaded binary's asset-specific terms are
unresolved.

- [ ] **Step 2: Run the compliance and deployment static tests**

```bash
bash deploy/supermemory/tests/test-public-source-compliance.sh
bash deploy/supermemory/tests/verify-compose.sh
```

Expected: both PASS.

- [ ] **Step 3: Commit license packaging**

```bash
git add apps/local-gateway/Dockerfile apps/memory-graph-playground/Dockerfile deploy/supermemory/Dockerfile.engine
git commit -m "fix(deploy): package source license notices"
```

### Task 4: Run the complete pre-deletion gate

**Files:**
- Verify only; no intended file modifications.

**Interfaces:**
- Consumes: the complete prepared Supermemory-based branch.
- Produces: recorded evidence that the branch is safe to publish before deleting the old repository.

- [ ] **Step 1: Run targeted tests and type checks**

```bash
bun test apps/local-gateway/src \
  tools/ram0-migration/src \
  integrations/coding-agents \
  apps/memory-graph-playground/src/app/api/routes.test.ts

bunx tsc --noEmit -p apps/local-gateway/tsconfig.json
bunx tsc --noEmit -p tools/ram0-migration/tsconfig.json
bunx tsc --noEmit -p apps/memory-graph-playground/tsconfig.json \
  --incremental --tsBuildInfoFile /tmp/ram0-graph-repo-replacement.tsbuildinfo

bash deploy/supermemory/tests/verify-compose.sh
```

Expected: all tests and type checks PASS without warnings that invalidate evidence.

- [ ] **Step 2: Check formatting, change scope, and tracked secrets**

```bash
git diff --check upstream/main...HEAD
git status --short
git diff --name-status upstream/main...HEAD
git grep -n -I -E '(BEGIN (RSA |OPENSSH |EC )?PRIVATE KEY|Bearer[[:space:]]+[[:alnum:]_.-]{32,}|sm_[[:alnum:]_-]{24,})' HEAD -- ':!bun.lock'
```

Expected: `git diff --check` is silent; status is clean; changed files are the
reviewed Ram0 delta; the secret scan returns no credential material. Review any
match manually rather than printing surrounding secret-bearing files.

- [ ] **Step 3: Refresh upstream and verify ancestry**

```bash
git fetch upstream --prune --tags
git rev-list --left-right --count upstream/main...HEAD
git merge-base --is-ancestor upstream/main HEAD
```

Expected: the count is `0 N`, with `N` equal to the reviewed Ram0 commit count,
and the ancestry check exits 0. If upstream advanced, stop and integrate it in a
separate reviewed sync before repository deletion.

- [ ] **Step 4: Push the prepared branch to the old remote**

```bash
git push origin HEAD:feat/ram0-supermemory-cutover
git ls-remote origin refs/heads/feat/ram0-supermemory-cutover
git rev-parse HEAD
```

Expected: the remote and local commit IDs match.

### Task 5: Create and verify the local GitHub backup

**Files:**
- Create outside repository: `/home/olhapi/projects/ram0-github-backup-<UTC timestamp>/`

**Interfaces:**
- Consumes: the current `olhapi/ram0` GitHub repository before deletion.
- Produces: verified `repository.git`, `ram0.bundle`, metadata JSON, ref records, and checksums retained locally.

- [ ] **Step 1: Create a private backup directory and mirror**

```bash
backup=/home/olhapi/projects/ram0-github-backup-$(date -u +%Y%m%dT%H%M%SZ)
mkdir -m 700 "$backup"
git clone --mirror https://github.com/olhapi/ram0.git "$backup/repository.git"
git -C "$backup/repository.git" bundle create "$backup/ram0.bundle" --all
```

- [ ] **Step 2: Export available non-secret GitHub metadata**

```bash
gh repo view olhapi/ram0 --json nameWithOwner,description,homepageUrl,isFork,parent,defaultBranchRef,visibility,url,createdAt,updatedAt,issues,pullRequests,stargazerCount,forkCount >"$backup/repository.json"
gh api repos/olhapi/ram0/branches --paginate >"$backup/branches.json"
gh api repos/olhapi/ram0/tags --paginate >"$backup/tags.json"
gh api repos/olhapi/ram0/releases --paginate >"$backup/releases.json"
gh api repos/olhapi/ram0/rulesets --paginate >"$backup/rulesets.json" || true
gh api repos/olhapi/ram0/environments --paginate >"$backup/environments.json" || true
gh api repos/olhapi/ram0/hooks --paginate >"$backup/hooks.json" || true
gh api repos/olhapi/ram0/keys --paginate >"$backup/deploy-keys.json" || true
gh variable list --repo olhapi/ram0 --json name,updatedAt >"$backup/actions-variables.json" || true
gh secret list --repo olhapi/ram0 --json name,updatedAt >"$backup/actions-secret-names.json" || true
git ls-remote https://github.com/olhapi/ram0.git >"$backup/remote-refs.txt"
git rev-parse HEAD >"$backup/cutover-head.txt"
```

Never run a command that exports secret values. GitHub secret APIs and `gh
secret list` expose names/metadata only.

- [ ] **Step 3: Validate and seal the backup**

```bash
git -C "$backup/repository.git" fsck --full
git bundle verify "$backup/ram0.bundle"
cutover_head=$(cat "$backup/cutover-head.txt")
git -C "$backup/repository.git" cat-file -e "$cutover_head^{commit}"
git -C "$backup/repository.git" cat-file -e "refs/heads/main^{commit}"
find "$backup" -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum >"$backup/SHA256SUMS"
chmod -R go-rwx "$backup"
```

Expected: `fsck`, bundle verification, and both `cat-file` checks pass. Record
the explicit backup path before proceeding.

### Task 6: Replace the GitHub repository

**Files:**
- External state: `https://github.com/olhapi/ram0`

**Interfaces:**
- Consumes: the verified backup and prepared cutover commit.
- Produces: a public GitHub fork whose parent is `supermemoryai/supermemory` and whose default branch is the Ram0 commit.

- [ ] **Step 1: Reconfirm the destructive preconditions**

```bash
git status --short
git rev-parse HEAD
cat "$backup/cutover-head.txt"
test "$(git rev-parse HEAD)" = "$(cat "$backup/cutover-head.txt")"
git -C "$backup/repository.git" fsck --full
git bundle verify "$backup/ram0.bundle"
```

Expected: clean status, matching commit IDs, and valid backup. Stop on any mismatch.

- [ ] **Step 2: Delete and recreate the repository**

```bash
gh repo delete olhapi/ram0 --yes
gh repo fork supermemoryai/supermemory \
  --fork-name ram0 \
  --default-branch-only \
  --clone=false \
  --remote=false
```

If deletion lacks `delete_repo`, run `gh auth refresh -s delete_repo`
interactively without printing the credential, then retry. GitHub may take a
few seconds to release the old name; retry the identical fork command for up to
60 seconds without changing its target. If recreation still fails, restore from
the verified mirror according to the spec before doing other work.

- [ ] **Step 3: Verify the new parent before pushing**

```bash
gh repo view olhapi/ram0 --json isFork,parent,defaultBranchRef,visibility,url
```

Expected: `isFork` is true, parent is `supermemoryai/supermemory`, default branch
is `main`, visibility is `PUBLIC`, and the URL is unchanged.

- [ ] **Step 4: Push only the Ram0 main history**

```bash
git push origin HEAD:main
```

Expected: fast-forward from the fork's upstream `main`. Do not use `--force`.

- [ ] **Step 5: Update public metadata**

```bash
gh repo edit olhapi/ram0 \
  --description 'Ram0 — an unofficial self-hosted Supermemory fork with MCP, graph UI, coding-agent integrations, and Mem0 migration tooling.' \
  --homepage '' \
  --default-branch main \
  --delete-branch-on-merge \
  --enable-merge-commit \
  --enable-rebase-merge \
  --enable-squash-merge

gh repo edit olhapi/ram0 \
  --add-topic supermemory \
  --add-topic mcp \
  --add-topic self-hosted \
  --add-topic codex \
  --add-topic claude-code \
  --add-topic ai-memory
```

### Task 7: Verify the recreated fork and normalize the local checkout

**Files:**
- Local Git refs only; no source changes intended.

**Interfaces:**
- Consumes: recreated GitHub fork.
- Produces: verified remote state, a fresh-clone proof, and local `main` tracking `origin/main` with `upstream` intact.

- [ ] **Step 1: Fetch and compare exact commits**

```bash
git fetch origin --prune
git rev-parse HEAD
git rev-parse origin/main
git rev-list --left-right --count upstream/main...origin/main
gh repo view olhapi/ram0 --json nameWithOwner,description,homepageUrl,isFork,parent,defaultBranchRef,repositoryTopics,visibility,url
```

Expected: local HEAD equals `origin/main`; GitHub shows the Supermemory parent,
Ram0 description/topics, public visibility, and `main` default.

- [ ] **Step 2: Verify from a fresh clone**

```bash
verify_dir=$(mktemp -d)
git clone --single-branch --branch main https://github.com/olhapi/ram0.git "$verify_dir/ram0"
git -C "$verify_dir/ram0" fsck --full
test "$(git -C "$verify_dir/ram0" rev-parse HEAD)" = "$(git rev-parse HEAD)"
bash "$verify_dir/ram0/deploy/supermemory/tests/test-public-source-compliance.sh"
test -f "$verify_dir/ram0/integrations/coding-agents/install.mjs"
```

Expected: all checks pass. Remove only this exact temporary directory afterward.

- [ ] **Step 3: Normalize local branch names without losing the old history**

```bash
git branch -m main mem0-main-local
git branch -m feat/ram0-supermemory-cutover main
git branch --set-upstream-to=origin/main main
git remote set-url upstream https://github.com/supermemoryai/supermemory.git
```

The full old history remains in the external mirror and bundle. Retain
`mem0-main-local` until final verification; do not push it to the recreated fork.

- [ ] **Step 4: Confirm package privacy and live service health without secrets**

```bash
gh api /user/packages/container/ram0-supermemory-engine --jq '.visibility'
gh api /user/packages/container/ram0-supermemory-gateway --jq '.visibility'
gh api /user/packages/container/ram0-supermemory-graph --jq '.visibility'
curl --fail --silent --show-error https://brain-api.olhapi.com/health >/dev/null
```

Expected: all engine-containing/downstream images remain `private`; health exits
0. Do not request or print container logs or authorization headers.

- [ ] **Step 5: Run the final local gate and commit-status check**

```bash
git status --short --branch
git diff --check upstream/main...main
git rev-list --left-right --count upstream/main...main
bash deploy/supermemory/tests/verify-compose.sh
```

Expected: clean `main` tracking `origin/main`, no whitespace errors, 0 commits
behind upstream, and all deployment/compliance checks passing.

### Task 8: Publish workstation migration instructions

**Files:**
- Modify: `integrations/coding-agents/README.md`
- Test: `deploy/supermemory/tests/test-public-source-compliance.sh`

**Interfaces:**
- Consumes: the stable `olhapi/ram0` URL and `main` branch.
- Produces: safe update instructions for existing Ram0 workstations that retain local backups and never expose keys on command lines.

- [ ] **Step 1: Add an “Existing Ram0 workstations” section**

Document this sequence:

```bash
git clone https://github.com/olhapi/ram0.git ~/projects/ram0-supermemory
cd ~/projects/ram0-supermemory
node integrations/coding-agents/install.mjs \
  --base-url https://brain-api.olhapi.com \
  --force
source ~/.config/ram0-supermemory/activate.sh
```

State that users must securely transfer or recreate the mode-`0600`
`credentials.env`, leave their old checkout/config untouched until verification,
remove `ram0@ram0-plugins` only after the new status check passes, and restart
Claude Code/Codex from an activated shell.

- [ ] **Step 2: Run documentation and integration tests**

```bash
bash deploy/supermemory/tests/test-public-source-compliance.sh
bun test integrations/coding-agents
```

Expected: PASS.

- [ ] **Step 3: Commit and push**

```bash
git add integrations/coding-agents/README.md
git commit -m "docs: add Ram0 workstation migration"
git push origin main
```

- [ ] **Step 4: Final remote verification**

```bash
test "$(git rev-parse HEAD)" = "$(git ls-remote origin refs/heads/main | cut -f1)"
gh repo view olhapi/ram0 --json description,isFork,parent,defaultBranchRef,repositoryTopics,visibility,url
git status --short --branch
```

Expected: exact local/remote commit match, correct Supermemory parent and metadata,
and clean status.
