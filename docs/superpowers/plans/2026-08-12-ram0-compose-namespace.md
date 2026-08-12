# Ram0 Compose Namespace Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate production Docker resources from the legacy Mem0 Compose namespace to exact Ram0 names without losing database/history data.

**Architecture:** Keep internal Compose service keys stable, but make the production override name the project `ram0`, pin exact container names, and name the bridge `ram0_network`. Extend the guarded deployer with a one-time source-project migration: it snapshots the legacy Compose files before checkout update, stops the source project without deleting host data, starts the target PostgreSQL first, and rolls back with the saved source Compose files on failure.

**Tech Stack:** Docker Compose v2, Bash, PostgreSQL/pgvector, existing `server/scripts/deploy_unraid.sh` deployment guard.

## Global Constraints

- Exact production resource names are `ram0_network`, `ram0_api`, `ram0_dashboard`, and `ram0_postgres`.
- Preserve `/mnt/user/appdata/mem0/data/postgres` and `/mnt/user/appdata/mem0/data/history`; do not retain legacy containers/networks after success.
- Preserve unrelated containers and the untracked Unraid override/Dockerfile.
- PostgreSQL must remain internal-only; deployment images remain immutable SHA digests and revision-labelled.

---

### Task 1: Define exact Ram0 production resources

**Files:**

- Modify: `server/docker-compose.unraid.yaml`
- Test: `server/scripts/deploy_unraid.sh --self-test`

**Interfaces:**

- Produces: production Compose project `ram0`, API `ram0_api`, dashboard `ram0_dashboard`, PostgreSQL `ram0_postgres`, and network `ram0_network`.
- Consumes: unchanged internal service keys `mem0`, `postgres`, `mem0-dashboard` and host-bound volumes.

- [ ] **Step 1: Add a failing structural assertion to the deployer self-test**

Extend `self_test` so a rendered target Compose file must contain the exact target project resource names and reject a `mem0_` network name.

- [ ] **Step 2: Run the self-test to establish RED**

Run `bash server/scripts/deploy_unraid.sh --self-test`.

Expected: FAIL because the Unraid Compose project currently declares `name: mem0` and does not set exact container/network names.

- [ ] **Step 3: Update the Unraid Compose override**

Set `name: ram0`; add `container_name` for each production service; override the existing `mem0_network` name to `ram0_network`. Do not change service keys or host data mounts.

- [ ] **Step 4: Run structural validation**

Run `docker compose -f server/docker-compose.yaml -f server/docker-compose.unraid.yaml config` with placeholder non-secret image/environment values and inspect that the rendered names are exact.

- [ ] **Step 5: Commit**

Run `git add server/docker-compose.unraid.yaml server/scripts/deploy_unraid.sh && git commit -m "feat(deploy): name Ram0 Compose resources"`.

### Task 2: Add a one-time legacy-project migration and rollback path

**Files:**

- Modify: `server/scripts/deploy_unraid.sh`
- Test: `server/scripts/deploy_unraid.sh --self-test`
- Document: `server/README.md`

**Interfaces:**

- Produces: `deploy_unraid.sh <sha> [legacy-compose-directory]`; the optional directory contains pre-migration `docker-compose.yaml` and `docker-compose.unraid.yaml` and is accepted only when source project `mem0` exists and target project `ram0` does not.
- Consumes: current immutable image state, existing source PostgreSQL, and source Compose files copied before host checkout updates.

- [ ] **Step 1: Add failing migration contract checks**

Extend `self_test` with a legacy source/target predicate and assertions that target operations use project `ram0`, source rollback uses project `mem0`, and the exact-name verifier rejects an incorrect container or network name.

- [ ] **Step 2: Run the self-test to establish RED**

Run `bash server/scripts/deploy_unraid.sh --self-test`.

Expected: FAIL because project selection and legacy migration arguments do not yet exist.

- [ ] **Step 3: Implement project-scoped Compose helpers**

Introduce constants `TARGET_PROJECT=ram0` and `LEGACY_PROJECT=mem0`; make target `compose_with_state` pass `-p "$TARGET_PROJECT"`; make source detection/backup select legacy containers only for migration; add a legacy Compose helper that uses the supplied saved files and `-p "$LEGACY_PROJECT"` for rollback.

- [ ] **Step 4: Implement ordered namespace migration**

When legacy mode is active: resolve/verify target images, render target, stop/remove only the legacy project without `-v`, start `ram0_postgres`, wait for its health, run Alembic, then start `ram0_api` and `ram0_dashboard`. On failure, remove the partial target project and recreate legacy PostgreSQL/API/dashboard from the saved Compose files and prior immutable state.

- [ ] **Step 5: Add exact live verifier and operator documentation**

Make `verify_deployment` require exact container/network names, migration revision, no Postgres port, image revisions, and no surviving legacy project resource after a successful migration. Document the one-time pre-checkout snapshot and deployment invocation in `server/README.md`.

- [ ] **Step 6: Run local verification**

Run `bash server/scripts/deploy_unraid.sh --self-test` and `git diff --check`.

Expected: both pass.

- [ ] **Step 7: Commit**

Run `git add server/scripts/deploy_unraid.sh server/README.md && git commit -m "feat(deploy): migrate legacy Compose namespace"`.

### Task 3: Release and execute the one-time migration

**Files:**

- No source changes.
- Host state: `/mnt/user/appdata/mem0/repo/server`, `/mnt/user/appdata/mem0/deploy/legacy-compose-<timestamp>`.

**Interfaces:**

- Consumes: pushed revision image tags and saved legacy Compose files.
- Produces: only `ram0_*` Docker resources with production health verification.

- [ ] **Step 1: Push the verified source revision and wait for both GHCR images**

Run `git push origin main`, then wait for the `Publish Ram0 images` workflow for that exact SHA to succeed.

- [ ] **Step 2: Snapshot legacy Compose files before updating the host checkout**

On Unraid, create a root-only timestamped directory under `/mnt/user/appdata/mem0/deploy/`, copy the current base and Unraid Compose files into it, and confirm both source containers/network have legacy `mem0` names.

- [ ] **Step 3: Fast-forward only the host repository**

Run `git pull --ff-only origin main` in `/mnt/user/appdata/mem0/repo`; verify the untracked override and Dockerfile remain present and do not stage or overwrite `.env`.

- [ ] **Step 4: Run the guarded migration**

Run `server/scripts/deploy_unraid.sh <exact-sha> <legacy-compose-directory>` as root. Expected: verified database backup, legacy project removal without volume deletion, target PostgreSQL health, migration, target API/dashboard startup, exact-name verification.

- [ ] **Step 5: Verify final live state**

Read-only verify the four exact Ram0 resource names, API/dashboard revision labels, health endpoints, network membership, lack of PostgreSQL ports, absence of legacy `mem0` containers/networks, and original database content/migration revision.
