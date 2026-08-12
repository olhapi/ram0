# Public GHCR and Unraid Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish public immutable Ram0 API/dashboard images from GitHub and migrate the existing Unraid deployment to a guarded digest-pinned upgrade command.

**Architecture:** A SHA-pinned GitHub Actions workflow builds the existing API and dashboard Dockerfiles for `linux/amd64` and publishes them to GHCR. A tracked production Compose overlay consumes digest references from a mode-600 state file, while one shell command owns validation, backup, migration, recreation, verification, state promotion, and rollback.

**Tech Stack:** GitHub Actions, Docker Buildx, GHCR, Docker Compose, Bash, PostgreSQL/pg_dump, Alembic, existing Ram0 container and test tooling.

## Global Constraints

- Publish `ghcr.io/olhapi/ram0-api` and `ghcr.io/olhapi/ram0-dashboard` as public `linux/amd64` packages.
- Deploy only immutable `@sha256:` references; do not deploy `latest`.
- Pin every third-party GitHub Action to a full immutable commit SHA.
- Give the workflow only `contents: read` and `packages: write` permissions.
- Add no dependency or testing library.
- Preserve `/mnt/user/appdata/mem0/data`, the existing mode-600 `.env`, and reverse-proxy URLs.
- Bind API/dashboard only to `192.168.1.2`; publish no PostgreSQL port.
- Never log credentials, `.env`, database dumps, tokens, or request bodies.
- Back up before mutation, retain backups, and restore the prior database revision and image digests on failure.

---

### Task 1: Public immutable GHCR workflow

**Files:**
- Create: `.github/workflows/ghcr-images.yml`
- Create: `tests/server/test_ghcr_workflow.py`
- Modify: `server/Dockerfile`
- Modify: `server/dashboard/Dockerfile`

**Interfaces:**
- Consumes: existing `server/Dockerfile`, `server/dashboard/Dockerfile`, and GitHub-provided `github.sha`/`GITHUB_TOKEN`.
- Produces: public SHA-tagged `ghcr.io/olhapi/ram0-api` and `ghcr.io/olhapi/ram0-dashboard` images with OCI `source`, `revision`, and `license` labels.

- [ ] **Step 1: Write workflow contract tests**

Add stdlib text assertions that require:

```python
WORKFLOW = Path(".github/workflows/ghcr-images.yml").read_text()

def test_ghcr_workflow_is_sha_pinned_and_minimally_privileged():
    assert "contents: read" in WORKFLOW
    assert "packages: write" in WORKFLOW
    assert "actions/checkout@11d5960a326750d5838078e36cf38b85af677262" in WORKFLOW
    assert "docker/login-action@c94ce9fb468520275223c153574b00df6fe4bcc9" in WORKFLOW
    assert "docker/setup-buildx-action@8d2750c68a42422c14e847fe6c8ac0403b4cbd6f" in WORKFLOW
    assert "docker/build-push-action@10e90e3645eae34f1e60eeb005ba3a3d33f178e8" in WORKFLOW
    assert "@v" not in WORKFLOW

def test_ghcr_workflow_publishes_both_amd64_sha_images():
    assert "ghcr.io/olhapi/ram0-api:sha-${{ github.sha }}" in WORKFLOW
    assert "ghcr.io/olhapi/ram0-dashboard:sha-${{ github.sha }}" in WORKFLOW
    assert WORKFLOW.count("platforms: linux/amd64") == 2
    assert WORKFLOW.count("org.opencontainers.image.revision=${{ github.sha }}") == 2
```

- [ ] **Step 2: Run the focused tests and capture RED**

Run:

```bash
uv run --isolated --offline --python 3.12 --with pytest pytest tests/server/test_ghcr_workflow.py -v
```

Expected: failure because `.github/workflows/ghcr-images.yml` does not exist.

- [ ] **Step 3: Add deterministic OCI labels to both Dockerfiles**

Add build arguments and labels to each final image:

```dockerfile
ARG VCS_REF
ARG SOURCE_URL=https://github.com/olhapi/ram0
LABEL org.opencontainers.image.source=$SOURCE_URL \
      org.opencontainers.image.revision=$VCS_REF \
      org.opencontainers.image.licenses=Apache-2.0
```

For the multi-stage dashboard Dockerfile, declare `ARG` again in the final `runner` stage before using it.

- [ ] **Step 4: Add the publishing workflow**

Create a workflow triggered by `push` to `main` and `workflow_dispatch`, with top-level permissions:

```yaml
permissions:
  contents: read
  packages: write
```

Use the four immutable Action SHAs asserted above. Log in to `ghcr.io` with `${{ github.actor }}` and `${{ secrets.GITHUB_TOKEN }}`. Build API from repository context with `server/Dockerfile`; build dashboard from `server/dashboard`. Each build passes `VCS_REF=${{ github.sha }}`, pushes only `sha-${{ github.sha }}`, targets `linux/amd64`, and uses scoped GitHub Actions cache entries `ram0-api` and `ram0-dashboard`.

- [ ] **Step 5: Run focused and existing container-definition checks**

Run:

```bash
uv run --isolated --offline --python 3.12 --with pytest pytest tests/server/test_ghcr_workflow.py tests/server/test_category_container_assets.py -v
ruff check tests/server/test_ghcr_workflow.py
git diff --check
```

Expected: all pass.

- [ ] **Step 6: Commit Task 1**

```bash
git add .github/workflows/ghcr-images.yml server/Dockerfile server/dashboard/Dockerfile tests/server/test_ghcr_workflow.py
git commit -m "ci: publish Ram0 images to GHCR"
```

---

### Task 2: Digest-only Unraid Compose contract

**Files:**
- Create: `server/docker-compose.unraid.yaml`
- Modify: `tests/server/test_category_container_assets.py`
- Modify: `server/README.md`

**Interfaces:**
- Consumes: `RAM0_API_IMAGE`, `RAM0_DASHBOARD_IMAGE`, `RAM0_REVISION`, and the existing `server/.env`.
- Produces: a rendered `mem0` Compose project with immutable application images, preserved host data, exactly two LAN-bound ports, and no build/source-mount behavior.

- [ ] **Step 1: Extend container asset tests with the production overlay contract**

Add stdlib assertions and a rendered-Compose subprocess test using sentinel digest references:

```python
env.update({
    "RAM0_API_IMAGE": "ghcr.io/olhapi/ram0-api@sha256:" + "a" * 64,
    "RAM0_DASHBOARD_IMAGE": "ghcr.io/olhapi/ram0-dashboard@sha256:" + "b" * 64,
    "RAM0_REVISION": "c" * 40,
})
```

Assert the rendered model has:

```python
assert services["mem0"]["image"] == env["RAM0_API_IMAGE"]
assert services["mem0-dashboard"]["image"] == env["RAM0_DASHBOARD_IMAGE"]
assert "build" not in services["mem0"]
assert "build" not in services["mem0-dashboard"]
assert services["postgres"].get("ports", []) == []
assert services["mem0"]["ports"][0]["host_ip"] == "192.168.1.2"
assert services["mem0-dashboard"]["ports"][0]["host_ip"] == "192.168.1.2"
assert all(volume["target"] != "/app" for volume in services["mem0"]["volumes"])
```

- [ ] **Step 2: Run the focused test and capture RED**

Run:

```bash
uv run --isolated --offline --python 3.12 --with pytest pytest tests/server/test_category_container_assets.py -v
```

Expected: failure because the Unraid overlay does not exist.

- [ ] **Step 3: Create the production overlay**

Set project name `mem0`. Override application builds with `build: !reset null`, consume the required digest variables via `${RAM0_API_IMAGE:?Set RAM0_API_IMAGE}` and `${RAM0_DASHBOARD_IMAGE:?Set RAM0_DASHBOARD_IMAGE}`, keep only the history host mount for the API, and run:

```yaml
command: >
  sh -c "alembic upgrade head && exec uvicorn main:app --host 0.0.0.0 --port 8000"
```

Use `unless-stopped`, bind `192.168.1.2:18888:8000` and `192.168.1.2:13000:3000`, remove the PostgreSQL publication, and retain:

```yaml
NEXT_PUBLIC_API_URL: https://api.example.invalid
API_INTERNAL_URL: http://mem0:8000
NEXT_PUBLIC_INSTANCE_NAME: Ram0
DASHBOARD_URL: https://dashboard.example.invalid
```

Mount `/mnt/user/appdata/mem0/data/history` and `/mnt/user/appdata/mem0/data/postgres`; retain the existing init script read-only.

- [ ] **Step 4: Document the state-file and render command**

Document mode-600 `/mnt/user/appdata/mem0/deploy/current.env` with complete digest references and the full revision, plus:

```bash
docker compose \
  --env-file /mnt/user/appdata/mem0/repo/server/.env \
  --env-file /mnt/user/appdata/mem0/deploy/current.env \
  -f docker-compose.yaml \
  -f docker-compose.unraid.yaml \
  config --quiet
```

- [ ] **Step 5: Run contract checks**

Run the focused test, both representative digest renders, `docker compose ... config --quiet`, Ruff on the modified Python test, and `git diff --check`.

Expected: all pass; rendered PostgreSQL has no published port and application services have no build definitions.

- [ ] **Step 6: Commit Task 2**

```bash
git add server/docker-compose.unraid.yaml server/README.md tests/server/test_category_container_assets.py
git commit -m "feat(server): add digest-pinned Unraid runtime"
```

---

### Task 3: Guarded Unraid upgrade and rollback command

**Files:**
- Create: `server/scripts/deploy_unraid.sh`
- Create: `tests/server/test_unraid_deploy_script.py`
- Modify: `server/README.md`

**Interfaces:**
- Consumes: one 40-character lowercase hexadecimal Git SHA, public GHCR tags `sha-<SHA>`, existing `.env`, Docker/Compose, and the current deployment state.
- Produces: atomically promoted `current.env`, retained `previous.env`, timestamped root-only backup, verified services, or a verified rollback.

- [ ] **Step 1: Write static and self-test contract tests**

Require the script to be executable and contain named phases/functions:

```python
for name in (
    "acquire_lock", "preflight", "backup_database", "resolve_images",
    "verify_image", "render_candidate", "migrate_database",
    "recreate_services", "verify_deployment", "rollback_deployment",
):
    assert f"{name}()" in SCRIPT
```

Run `bash -n`, optional installed `shellcheck`, and `deploy_unraid.sh --self-test`. The self-test must use temporary directories and command doubles to prove invalid SHA rejection, concurrent-lock refusal, digest/revision mismatch rejection, secret redaction, atomic state promotion, rollback ordering, and cleanup without Docker or SSH.

- [ ] **Step 2: Run tests and capture RED**

Expected: failure because `server/scripts/deploy_unraid.sh` does not exist.

- [ ] **Step 3: Implement validated inputs, ownership, and command boundaries**

Use `set -Eeuo pipefail`, fixed paths rooted at `/mnt/user/appdata/mem0`, and a fixed atomic lock directory. Accept only `^[0-9a-f]{40}$`. Resolve all destructive targets to exact project/state/backup paths before mutation. Route Docker, curl, and PostgreSQL commands through small functions so self-test doubles cannot execute the real tools.

- [ ] **Step 4: Implement backup and candidate resolution**

Before mutation:

- verify `.env` is root-owned mode 600;
- verify current project ownership and port bindings;
- write `pg_dump --format=custom` to a mode-600 timestamped backup and validate it with `pg_restore --list`;
- copy current/previous state and Compose overlays into that backup;
- pull both `sha-<SHA>` tags;
- resolve `RepoDigests` with `docker image inspect`;
- require `linux/amd64` and OCI revision equal to the argument;
- write candidate digest variables with `umask 077`.

- [ ] **Step 5: Implement migration, recreation, and verification**

Render the candidate configuration before migration. Run Alembic from the candidate API digest on the existing network/database, recreate only `mem0`, `mem0-dashboard`, and required project dependencies, then verify with bounded polling:

- Alembic head is `007` and category tables/indexes exist;
- direct API docs/OpenAPI succeeds;
- dashboard health, root, and hashed asset succeed;
- `api.example.invalid` and `dashboard.example.invalid` proxy responses succeed;
- PostgreSQL publishes no host port;
- actual container image IDs match the candidate digests;
- unrelated container IDs captured before deployment remain unchanged.

Promote candidate to `current.env` with a same-filesystem rename only after all checks pass; copy the old current state to `previous.env` first.

- [ ] **Step 6: Implement rollback and first-migration handling**

On post-mutation failure, stop the candidate application services, determine the recorded prior Alembic revision, and use the candidate API image to downgrade from `007` to `006` when required. If downgrade fails, restore the verified database dump while application services are stopped. Recreate the previous digest state and require the same health checks before reporting rollback success. Never delete backups automatically.

- [ ] **Step 7: Document the single operational command**

Add:

```bash
sudo server/scripts/deploy_unraid.sh <full-git-commit-sha>
```

Document prerequisites, output, backup location, retained state files, and manual recovery entry points without showing secret values.

- [ ] **Step 8: Run focused and regression checks**

Run:

```bash
bash -n server/scripts/deploy_unraid.sh
shellcheck server/scripts/deploy_unraid.sh
server/scripts/deploy_unraid.sh --self-test
uv run --isolated --offline --python 3.12 --with pytest pytest tests/server/test_unraid_deploy_script.py tests/server/test_category_container_assets.py -v
ruff check tests/server/test_unraid_deploy_script.py tests/server/test_category_container_assets.py
git diff --check
```

Expected: all pass. If `shellcheck` is unavailable, record that fact and do not install a new dependency.

- [ ] **Step 9: Commit Task 3**

```bash
git add server/scripts/deploy_unraid.sh tests/server/test_unraid_deploy_script.py server/README.md
git commit -m "feat(server): automate guarded Unraid upgrades"
```

---

### Task 4: Publish publicly and migrate the live Unraid deployment

**Files:**
- Modify only if verification exposes a defect in Tasks 1-3.
- Update ignored evidence report: `.superpowers/sdd/2026-08-08-public-ghcr-unraid-deployment-report.md`

**Interfaces:**
- Consumes: committed/pushed Tasks 1-3, GitHub Actions, public GHCR, SSH access to `root@192.168.1.2`, and the existing backup `/mnt/user/appdata/mem0/backups/pre-ram0-20260808-121840`.
- Produces: public verified packages and a live digest-pinned Ram0 deployment at the requested Git commit.

- [ ] **Step 1: Run the complete local regression gate**

Run the established backend suite, Ruff, dashboard Prettier/typecheck/build, workflow/container/deploy tests, Compose render checks, docs coverage, and full `git diff --check`. Confirm no dependency manifests, lockfiles, `.env`, unrelated workflows, or Unraid data files changed.

- [ ] **Step 2: Push `main` and monitor the exact workflow run**

Push the reviewed commits, then use `gh run list`/`gh run watch` to require the `ghcr-images.yml` run for the exact HEAD SHA to succeed. Inspect bounded job logs without exposing the workflow token.

- [ ] **Step 3: Make both packages public**

For each package, run the GitHub Packages visibility API as the repository owner:

```bash
gh api --method PATCH /user/packages/container/ram0-api/visibility -f visibility=public
gh api --method PATCH /user/packages/container/ram0-dashboard/visibility -f visibility=public
```

Read both package records back and require `visibility == "public"`.

- [ ] **Step 4: Verify unauthenticated artifacts**

With no Docker registry credentials, pull both SHA tags, inspect `linux/amd64`, revision/source/license labels, and record their immutable repository digests. Remove only the temporary local verification tags afterward.

- [ ] **Step 5: Re-audit live Unraid immediately before mutation**

Over SSH, verify current containers, image IDs, database revision `006`, `.env` ownership/mode, data paths, free space, absence of another deployment lock, current proxy responses, and the existing root-only backup. Stop if live state differs materially from the design assumptions.

- [ ] **Step 6: Install tracked deployment files and run the guarded upgrade**

Fetch the exact published commit on Unraid without discarding the preserved untracked legacy override. Resolve and run that commit:

```bash
TARGET_SHA="$(git rev-parse HEAD)"
server/scripts/deploy_unraid.sh "$TARGET_SHA"
```

Do not manually recreate services while the command owns the lock.

- [ ] **Step 7: Independently verify the live result**

After the command exits zero, independently confirm:

- running image references equal the stored API/dashboard digests;
- database is at `007` and category schema exists;
- API/category and dashboard endpoints work directly over `192.168.1.2`;
- both reverse-proxy endpoints work and dashboard assets load;
- PostgreSQL is internal-only;
- persistent data remains present;
- no temporary candidate state or deployment lock remains;
- unrelated containers are unchanged.

- [ ] **Step 8: Record evidence and final repository state**

Write exact workflow URL, Git SHA, image digests, package visibility, backup path, migration result, live checks, and cleanup outcome to the ignored report. Confirm local and remote Git worktrees are clean except the explicitly preserved legacy Unraid override, and `origin/main` equals the deployed SHA.
