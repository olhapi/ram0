# Ram0 Self-Hosted Server

Ram0 ships a self-hosted FastAPI server plus a local dashboard. It is secure by default, supports dashboard login and API keys, and exposes OpenAPI docs at `/docs`.

> **Upgrading?** The Postgres image changed from the archived `ankane/pgvector:v0.5.1`
> to the official `pgvector/pgvector:pg17`, and `POSTGRES_PASSWORD` is now a required
> env var. If you have an existing install, see
> [Migrating from ankane/pgvector to pgvector/pgvector](#migrating-from-ankanepgvector-to-pgvectorpgvector)
> before running `docker compose up`.

## Quick Start

### Prerequisites

Copy the example env file and set a Postgres password (required):

```bash
cd server
cp .env.example .env
# Edit .env — at minimum set POSTGRES_PASSWORD, OPENAI_API_KEY, and JWT_SECRET
```

### Agent-first

Run one command; the terminal prints the admin email, password, and first API key.

```bash
cd server
make bootstrap
```

This starts the stack, waits for the API and dashboard to be ready, creates the first admin, and generates the first API key.

> The generated credentials print once in the `=== Ready ===` block. Save the password and API key before closing the terminal — the API key cannot be recovered afterwards.

> `make bootstrap` skips the setup wizard, so the use-case → custom-instructions step doesn't run. To add custom instructions afterwards, `POST /configure` with `{"custom_instructions": "..."}`, or run the Browser-first flow on a fresh install.

You can override the generated credentials:

```bash
cd server
make bootstrap EMAIL=admin@company.com PASSWORD='strong-password' NAME='Admin'
```

For machine-readable output:

```bash
cd server
OUTPUT=json make seed
```

Teardown:

```bash
# Stop the stack
cd server && make down

# Wipe all data (including the Postgres volume)
cd server && make clean
```

### Browser-first

Start the stack and finish setup by walking through the wizard in your browser.

```bash
cd server
make up
```

Then open `http://localhost:3000` and complete the setup wizard.

## Security Defaults

- Dashboard login uses JWTs.
- Programmatic access uses account-owned `X-API-Key` credentials. A key always
  acts as the account that created it.
- Auth is enabled by default.
- `AUTH_DISABLED=true` exists for local development only and should not be used in production.

### Environment contract

`POSTGRES_PASSWORD` and `JWT_SECRET` are required for a normal authenticated
Compose deployment. Configure the key for the LLM/embedder provider you use
(`OPENAI_API_KEY` in the default example). `JWT_SECRET` must be a unique,
high-entropy value and is never safe to commit. `ADMIN_API_KEY` is an optional
legacy/bootstrap compatibility credential; prefer individually owned API keys.
`AUTH_DISABLED` is optional and for local development only.

`DASHBOARD_URL` is optional and defaults to `http://localhost:3000`. In a
deployed installation, set it to the browser-visible dashboard origin. Ram0
validates it as exactly one canonical `http` or `https` origin: it cannot have
userinfo, a path other than an optional trailing slash, a query, a fragment,
whitespace, an ambiguous numeric hostname, or an inappropriate/default port.
The server uses this value for CORS and copied invitation links; an invalid
value prevents startup rather than producing a link to an unintended origin.

## MCP client setup

Ram0 exposes a Bearer-authenticated Streamable HTTP endpoint at `/mcp`. Create
an account-owned key on the dashboard, review the checkout, and permanently
configure the client bridge:

```bash
git clone https://github.com/olhapi/ram0.git ~/ram0-plugins/ram0
python3 ~/ram0-plugins/ram0/integrations/ram0-plugin/scripts/install_cli.py
ram0 setup --url 'https://ram0.example.lan'
ram0 config test
codex mcp add ram0 -- python3 ~/.local/share/ram0/mcp_stdio_adapter.py
```

`ram0 setup` stores exactly the URL and key at
`~/.config/ram0/config.json` (`0700` directory, `0600` file). The key is sent
only as `Authorization: Bearer` to Ram0. It is never stored as memory or logged
and never sent to telemetry or third parties. Do not put it in MCP JSON, the
server environment, source control, prompts, or plugin manifests. The endpoint
does not accept `X-API-Key` or caller-selected ownership.

Each MCP key acts as its owning Ram0 account. Its memory namespace is
account-wide rather than caller-selected: clients cannot choose another owner,
agent, or run scope. MCP is available only after ownership version 1 is ready;
if a legacy-data migration has not completed safely, resolve the server's
ownership-version readiness diagnostic before connecting a client.

### Git project memory scopes

`app_id` is an optional Git-project grouping inside the authenticated account.
It is compatible with Mem0-style app filters, but it is not a Ram0 project
resource, membership model, or security boundary. The authenticated account
remains the outer boundary for every read, write, entity operation, and reset;
two accounts may safely use the same `app_id` without sharing memories.

The full plugin resolves a normalized project ID for each hook event. Its
resolution order is `RAM0_PROJECT_ID`, a private saved mapping, the canonical
Git origin (host plus repository path), the Git repository name, then the
current directory name. Linked worktrees share the saved Git identity. Raw
paths, remotes, branches, credentials, and account identity are not stored in
memory metadata.

Normal plugin recall combines the current project with global memories. Normal
writes use the current project; an explicit `global` write omits `app_id` and
is visible account-wide. Existing memories without `app_id` therefore remain
global and need no migration. Direct MCP cannot inspect the calling agent's
working directory: pass the normalized `app_id` for default or `project`
operations, or choose `scope: "global"` explicitly.

The `/entities` app rows are derived buckets, not project objects. Deleting an
app entity deletes only memories with that `app_id` inside the authenticated
account; it cannot affect the same app name owned by another account.

The MCP surface contains exactly these six tools:

| Tool              | Use                                           |
| ----------------- | --------------------------------------------- |
| `remember`        | Store one piece of user-authored information. |
| `search_memories` | Find memories with a natural-language query.  |
| `list_memories`   | List the account's memories.                  |
| `get_memory`      | Retrieve an account-owned memory by UUID.     |
| `update_memory`   | Change an account-owned memory by UUID.       |
| `forget_memory`   | Delete an account-owned memory by UUID.       |

To confirm the client is working across tasks, call `remember` with a genuine
preference, start a new task in the client, then call `search_memories` for
that preference. The new task should find the remembered preference through
the same account-wide namespace.

### Full automation plugin

Direct MCP is tools-only: it exposes the six tools above and never installs
automatic retrieval, durable capture, or lifecycle hooks. For Claude Code,
Codex, Cursor, and OpenCode, the separate Ram0 plugin registers the same
Bearer-authenticated MCP endpoint and adds safe automation. Use the permanent
setup above, then install `ram0@ram0-plugins`. Claude Code, Codex, Cursor, and
OpenCode are supported; restart the client and review/trust Codex hooks in
`/hooks`.

The plugin starts retrieval and capture enabled. It captures only bounded,
locally selected decisions, preferences, architecture facts, and follow-ups;
it never sends or saves raw prompts, raw transcripts, file dumps, or
complete source/code/diff content. A missing key or unavailable endpoint fails
open, so the host agent continues with safe local status output.
Non-empty `RAM0_API_URL` and `RAM0_API_KEY` override their individual stored
fields for explicit development/CI processes only.

Install commands and the one-time Codex hook installer are documented in the
[Ram0 Agent Plugin guide](../docs/integrations/ram0-plugin.mdx). The plugin
uses `Authorization: Bearer`; its API-key owner has a private category catalog
copied from the legacy template on first access, and later owner edits are not
overwritten. The upstream `integrations/mem0-plugin` directory is intentionally
not modified; the Ram0 adaptation's maintenance procedure is in
[`integrations/ram0-plugin/UPSTREAM.md`](../integrations/ram0-plugin/UPSTREAM.md).

Migration and troubleshooting: remove the old `mem0-plugins` marketplace and
duplicate remote MCP entries before installing `ram0@ram0-plugins`; rotate with
`ram0 config set-key`; repair permissions with
`chmod 600 ~/.config/ram0/config.json`; rerun `ram0 setup` for missing config;
use `ram0 config show` and `ram0 config test` for an unreachable endpoint.

Maintainers can reproduce the live two-account acceptance path from a clean
checkout in two explicit phases. The preparation phase may build images and
contact registries; repeatable test runs are offline from prerequisite
validation through cleanup:

```bash
make -C server e2e-ram0-plugin-prepare  # once, with network access
make -C server e2e-ram0-plugin          # offline; repeat as needed
```

The offline target fails with the preparation command when a content-tagged
checkout image or pinned PostgreSQL image is missing or stale.

## Multi-user accounts and memory ownership

Every authenticated Ram0 account owns one isolated memory namespace. Its UUID
is the canonical `user_id` for every REST memory operation. Clients must not
send `user_id` as a body field, query value, or search filter: Ram0 rejects an
owner selector instead of ignoring it. `agent_id` and `run_id` remain optional
organization fields inside that account's namespace, not authorization scopes.

An administrator can manage account lifecycle but has no memory-read,
memory-write, deletion, or cross-owner category-catalog bypass. The legacy
global category catalog is a validated one-time template: first catalog access
creates an independent catalog for that owner, and later edits affect only that
owner. Catalog edits never rewrite historical labels. Missing and foreign memory
IDs intentionally have the same response. `/reset`, `/entities`, category
counts, category jobs, and reclassification are all restricted to the
authenticated owner's memories. Category jobs retain the owner UUID so their
operational data cannot expose another account's memory IDs.

API keys are owned by the account that creates them and enforce the identical
boundary as that account's JWT. Disabling a member immediately blocks that
member's existing access JWTs, refresh tokens, and API keys. Restoring the
member re-enables those credentials subject to their ordinary expiry and
revocation state. Administrators cannot disable themselves or another
administrator.

### Upgrade existing Mem0 data safely

Before replacing an existing Mem0 deployment with this Ram0 version, take and
verify a PostgreSQL backup using your deployment's normal protected credential
source. Do not paste database credentials into shell history, documentation,
or tickets. The backup is the rollback path.

On the standard upgrade path with one existing administrator, Ram0
automatically assigns every legacy memory and unowned category job to that
administrator. It preserves each
memory's ID, text, metadata, categories, vector, history, timestamps, and
agent/run identifiers while replacing only the memory owner with that admin's
account UUID. The operation is idempotent: an interrupted migration resumes on
the next start and marks ownership version 1 ready only after a complete
rescan verifies every memory and category job.

If legacy data exists before any account does, Ram0 waits without changing a
memory or category job. Bootstrap registration creates the first administrator
and automatically resumes the same ownership migration before setup completes.
If multiple accounts already exist, or the vector store cannot enumerate and
patch every payload safely, Ram0 refuses to guess and remains blocked. Memory
and invitation routes stay unavailable with an operator-facing maintenance
diagnostic until ownership version 1 can be completed safely. Do not invite
users until that condition is resolved.

### Invite members without email delivery

After ownership version 1 is ready, an administrator can create a member
invitation from **Users**. Ram0 returns a copied link once; it does not send
email. The link is valid for seven days, can be accepted once, and carries its
secret in a URL fragment so it is not sent in request paths. The dashboard
shows it only in the creation dialog with **Copy this link now. It won't be
shown again.** Closing the dialog clears it, and the pending-invitation list
has no recovery/copy control.

The server stores only a SHA-256 hash of the invitation token, never the raw
token. If a link is lost, revoke its pending invitation and create a new one;
Ram0 cannot recover the original. Invalid, expired, consumed, and revoked links
have the same generic failure response.

## Forgotten password

Reset an admin password from the host while the stack is running:

```bash
cd server
make reset-admin-password EMAIL=admin@example.com PASSWORD='new-strong-password'
```

This is the supported recovery path. Anyone with shell access to the host already has full access to the database and secrets, so this command does not expand the attack surface.

## Request log retention

The `request_logs` table is append-only and grows with traffic (~864k rows/day at 10 req/s). Prune it periodically:

```bash
cd server
make prune-logs                               # defaults to 30 days
make prune-logs REQUEST_LOG_RETENTION_DAYS=7  # shorter window
```

Wire the command into cron or a systemd timer in production. The `created_at` column uses a BRIN index, so range deletes stay cheap even on large tables.

## Local URLs

- Dashboard: `http://localhost:3000`
- API: `http://localhost:8888`
- OpenAPI docs: `http://localhost:8888/docs`

## Dashboard

Once logged in, the dashboard exposes:

- **Requests** — live audit log of API calls (method, path, status, latency).
- **Memories** — browse only the authenticated account's memories.
- **Entities** — list the authenticated owner's `agent_id` and `run_id` values with counts. Entity deletion is also
  authenticated-owner-only; public `user` entities are not exposed.
- **API Keys** — create, label, and revoke per-user keys.
- **Users** — administrators only: create/revoke member invitations and
  disable/restore members. Members do not see this navigation item.
- **Categories** — every authenticated user manages their own effective category catalog. The legacy global catalog is copied as
  a one-time template on first access; later edits affect only that user and never rewrite historical labels.
  Counts, reclassification previews and starts, and durable job monitoring are scoped to the authenticated
  user's own memories.
- **Configuration** — runtime LLM and embedder override. Changes persist to the app database and reapply on restart, layered over the values from your `.env`.
- **Settings** — account profile and password.

## Custom categories (Ram0)

Ram0 classifies each newly added or text-updated memory asynchronously with the server's currently configured memory
LLM. This is an additional LLM call after the core memory operation. Category work is auxiliary: a category queue or
provider failure does not roll back an otherwise successful memory add, update, delete, or reset.

The category feature is delivered only in Ram0's self-hosted REST server and dashboard. It is not an added API for the
upstream Python or TypeScript SDKs or the hosted Mem0 Platform. The complete HTTP contract and examples are in the
[REST API documentation](../docs/open-source/features/rest-api.mdx#custom-categories-ram0).

### Catalog precedence and defaults

Every classification uses exactly one catalog, selected in this order:

1. A non-empty `custom_categories` catalog on that `POST /memories` call.
2. A non-empty saved catalog for the authenticated owner.
3. The server defaults below when no owner definitions are saved.

The per-call catalog is not persisted. An explicit empty per-call catalog is rejected with HTTP 422; it does not mean
"use defaults." In contrast, replacing the saved catalog with the bare JSON array `[]` intentionally restores the
default fallback. Catalogs preserve definition order, allow at most 50 unique names, and require names matching
`^[a-z][a-z0-9_]*$` (1–64 characters) plus non-empty descriptions of at most 500 characters.

Catalog levels replace rather than merge with one another. A per-call list replaces the owner's saved/default catalog
for that call. Adding the first saved definition while defaults are active makes that saved list the complete active
catalog; deleting the final saved definition restores the full defaults. The legacy global catalog is a validated
one-time template: first owner access creates an independent copy, and later edits affect only that owner.
Catalog CRUD affects future classification only: existing jobs retain their catalog snapshots, existing labels are not
rewritten, and reclassification remains an explicit owner-scoped operation.

The default order and descriptions are:

| Order | Name                   | Description                                                          |
| ----: | ---------------------- | -------------------------------------------------------------------- |
|     1 | `personal_details`     | Identity, age, location, education, and personal background.         |
|     2 | `family`               | Family members, relationships, household, and family events.         |
|     3 | `professional_details` | Employment, career, workplace, skills, and professional goals.       |
|     4 | `sports`               | Sports played, followed, watched, or preferred.                      |
|     5 | `travel`               | Trips, destinations, travel plans, and travel preferences.           |
|     6 | `food`                 | Food, cooking, restaurants, diets, and dining preferences.           |
|     7 | `music`                | Artists, genres, instruments, concerts, and listening preferences.   |
|     8 | `health`               | Health conditions, care, wellness, fitness, and medical information. |
|     9 | `technology`           | Devices, software, technical interests, and technology preferences.  |
|    10 | `hobbies`              | Leisure activities, crafts, collections, and recurring interests.    |
|    11 | `fashion`              | Clothing, style, accessories, sizes, and fashion preferences.        |
|    12 | `entertainment`        | Films, television, books, games, and other media preferences.        |
|    13 | `milestones`           | Important achievements, anniversaries, transitions, and life events. |
|    14 | `user_preferences`     | General likes, dislikes, habits, choices, and preferred behavior.    |
|    15 | `misc`                 | Useful personal context that does not fit another active category.   |

Each durable job stores an immutable catalog snapshot. Editing, renaming, deleting, replacing, or resetting the
catalog affects future jobs only: **catalog changes do not retag historical memories**. Labels no longer present in the
active catalog remain visible as retired labels with counts until you explicitly reclassify the affected memories.

### Worker configuration

Set these variables on the API process. All numeric values must be positive; invalid values prevent category runtime
initialization.

| Variable                        | Default | Behavior                                                                                                                                                                                                                                                             |
| ------------------------------- | ------: | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `CATEGORY_WORKER_ENABLED`       |  `true` | Starts the in-process worker. Accepted true values are `1`, `true`, `yes`, and `on`; false values are `0`, `false`, `no`, and `off` (case-insensitive). When disabled, API calls can still create durable pending jobs for a later enabled worker.                   |
| `CATEGORY_WORKER_POLL_SECONDS`  |   `1.0` | Idle polling interval in seconds; must be a finite positive number.                                                                                                                                                                                                  |
| `CATEGORY_WORKER_LEASE_SECONDS` |    `60` | Processing lease and renewal duration in seconds; must be a positive integer.                                                                                                                                                                                        |
| `CATEGORY_WORKER_MAX_ATTEMPTS`  |     `3` | Recorded-attempt threshold applied after a handled processing error, including classifier/provider errors: retry below the threshold, otherwise fail. It is not a hard cap on claims because expired processing leases can be reclaimed. Must be a positive integer. |

The worker is single-threaded per API process and claims one job at a time. PostgreSQL row locking and a partial unique
index allow only one active (`queued`, `processing`, or `retrying`) job per memory across processes.

### States, retries, and restarts

A normal job moves `queued` → `processing` → `completed`. During uninterrupted processing with the default failure
threshold of 3, a handled processing error (including a classifier/provider error) after recorded attempt 1 moves the
job to `retrying` for 2 seconds; another such error after attempt 2 retries after 4 seconds; and an error at attempt 3
moves it to terminal `failed`. The backoff is `min(2^attempts, 60)` seconds when the recorded count remains below
`CATEGORY_WORKER_MAX_ATTEMPTS`.

`CATEGORY_WORKER_MAX_ATTEMPTS` is a failure threshold, not a hard total-claim guarantee. Each claim increments the
recorded `attempts` value, but the threshold is evaluated only when a claimed job reports a handled processing error
through the retry/fail transition. Classifier/provider errors are one example of that broader path.

The memory's top-level category fields expose a separate, simpler lifecycle:

- `categories: null`, `category_status: "pending"` while an active job is queued, processing, or retrying.
- `categories: [/* zero or more snapshotted catalog names */]`, `category_status: "completed"` after valid strict JSON
  is allowlisted.
- `categories: []`, `category_status: "failed"` after terminal failure, or immediately when durable enqueue fails.
- `categories: null`, `category_status: "unclassified"` for a legacy memory that has never entered this lifecycle.

Jobs can also become `cancelled`. Deleting a memory cancels matching active work; changing memory
text replaces its active job with a new hash/catalog snapshot. A metadata-only update does not reclassify. Before and
after calling the provider, the worker verifies the memory still exists and has the snapshotted hash, so stale work is
cancelled rather than overwriting newer content.
`/reset` deletes only the authenticated caller's memories and purges all category-job rows owned by that caller while
preserving every other owner's memories and jobs.

If an API process exits during `processing`, PostgreSQL retains the job and its lease. A restarted worker reclaims it
after `lease_expires_at` (60 seconds by default), increments `attempts`, and continues. Lease recovery does not itself
apply the failure threshold, so a crash can produce a later recorded attempt, including a count above the configured
threshold before a subsequent handled processing error evaluates it. The worker renews its still-valid lease
immediately before writing the memory payload; a worker that lost the lease does not write a result.

Provider exceptions and malformed responses are stored and exposed only as stable codes/messages such as
`provider_error` / `Category provider request failed`, `invalid_json` / `Invalid category response`, or the generic
`category_error` / `Category classification failed`. Raw provider output, catalog snapshots, memory hashes, and worker
identifiers are not returned by the jobs API.

### Explicit reclassification and cost preview

Catalog CRUD never starts a backfill. Use the dashboard or the authenticated owner REST flow when historical labels
must change:

1. `POST /categories/reclassify/preview` estimates work without creating jobs or calling the LLM.
2. `POST /categories/reclassify` starts work only when the body contains exact confirmation
   `"confirm": "RECLASSIFY"`.

Scope `unclassified_failed` (the default) selects only memories whose category status is `unclassified` or `failed`;
scope `all` selects every memory snapshot. Execution is idempotent for active work: a memory with an existing queued,
processing, or retrying job is counted in `skipped_active_jobs` rather than receiving another active job.

Preview token counts are deterministic estimates, not provider usage reports: input tokens are
`max(1, ceil(total classifier prompt characters / 4))` per memory, while output tokens are
`max(1, ceil(characters in the JSON response containing every catalog name / 4))`. Optional non-negative finite
`input_rate_per_million` and `output_rate_per_million` values must be supplied together. They are not persisted and no
provider price is assumed. When both are present:

```text
estimated_cost =
  (estimated_input_tokens * input_rate_per_million
   + estimated_output_tokens * output_rate_per_million) / 1,000,000
```

### Database migration

Migration `007` creates the durable `category_jobs` table and its claim/uniqueness indexes. The Compose API command
runs `alembic upgrade head` automatically at startup. To exercise the category migration directly from the `server/`
directory with its normal Postgres environment configured, run:

```bash
alembic upgrade head
alembic downgrade 006
alembic upgrade head
```

The downgrade removes `category_jobs` and its job history. It does not remove category values already stored in vector
payloads or the saved `custom_categories` value in the existing `settings` table. Stop category workers before a manual
downgrade, then re-upgrade before accepting category-enabled traffic.

### Security boundary

The classifier treats catalog descriptions and memory text as untrusted data, requires one exact JSON object, drops
unknown labels, and emits selected names in catalog order. These controls bound output, but categories remain
LLM-generated organizational metadata. **Never use categories for authorization, tenant isolation, data visibility, or
other security decisions.** Ram0 derives the account UUID (`user_id`) from authentication as the ownership boundary;
`agent_id` and `run_id` only organize data inside that account.

## Unraid image deployment

Ram0 publishes separate public API and dashboard images to GitHub Container Registry. The tracked
`docker-compose.unraid.yaml` overlay runs those images by immutable digest; it does not build source on Unraid and does
not require registry credentials.

Deployment state lives outside the checkout at `/mnt/user/appdata/mem0/deploy/current.env`, is owned by root with mode
`600`, and contains only immutable image references and the deployed revision:

```dotenv
RAM0_API_IMAGE=ghcr.io/olhapi/ram0-api@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
RAM0_DASHBOARD_IMAGE=ghcr.io/olhapi/ram0-dashboard@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
RAM0_REVISION=cccccccccccccccccccccccccccccccccccccccc
```

Render and validate the production configuration with:

```bash
cd /mnt/user/appdata/mem0/repo/server
docker compose \
  --env-file /mnt/user/appdata/mem0/repo/server/.env \
  --env-file /mnt/user/appdata/mem0/deploy/current.env \
  -f docker-compose.yaml \
  -f docker-compose.unraid.yaml \
  config --quiet
```

Set `RAM0_HOST_IP` in the root-only `server/.env` to an IPv4 address assigned to the Unraid host. Also set
`RAM0_PUBLIC_API_URL` and `RAM0_DASHBOARD_URL` to this instance's externally reachable canonical `http(s)` origins.
The overlay binds the API to `${RAM0_HOST_IP}:18888`, the dashboard to `${RAM0_HOST_IP}:13000`, and keeps PostgreSQL
internal-only on `ram0_network` (`POSTGRES_HOST=postgres`). Its exact Docker resource names are `ram0_api`,
`ram0_dashboard`, and `ram0_postgres`. It preserves the host PostgreSQL and history directories while removing the
development API source mount and Uvicorn reload mode. Use the guarded deployment command documented below for
upgrades; do not edit the state file during a deployment.

From the Unraid checkout, deploy one published full Git commit SHA with:

```bash
cd /mnt/user/appdata/mem0/repo
sudo server/scripts/deploy_unraid.sh 0123456789abcdef0123456789abcdef01234567
```

### One-time legacy namespace migration

If this host still has the previous `mem0` Compose project, snapshot its tracked
Compose files before fast-forwarding the checkout, then pass that snapshot as a
second argument. The command stops only the legacy project, never uses `-v`,
and recreates the stack as the exact Ram0 resources above against the same
host-backed PostgreSQL and history directories. On failure, it removes the
partial Ram0 project and recreates the legacy project from the snapshot.

```bash
cd /mnt/user/appdata/mem0/repo
stamp=$(date -u +%Y%m%d-%H%M%S)
legacy_dir="/mnt/user/appdata/mem0/deploy/legacy-compose-$stamp"
install -d -m 700 "$legacy_dir"
cp server/docker-compose.yaml server/docker-compose.unraid.yaml "$legacy_dir/"
git pull --ff-only origin main
sudo server/scripts/deploy_unraid.sh 0123456789abcdef0123456789abcdef01234567 "$legacy_dir"
```

After a successful migration, use the one-argument command for normal
upgrades. Do not delete the legacy snapshot until the new API and dashboard
have been verified.

The command acquires `/tmp/ram0-unraid-deploy.lock`, checks the live project, creates and validates a root-only custom
PostgreSQL dump under `/mnt/user/appdata/mem0/backups/`, resolves both public SHA tags to immutable digests, validates
their `linux/amd64` architecture and OCI revision, runs migrations, recreates the application services, and promotes
the new state only after direct and proxied health checks pass. It retains `previous.env` and the timestamped backup.

If verification fails after mutation, the command stops the candidate application, returns Alembic to the recorded
prior revision (including the first `007` to `006` rollback), and restarts the previous image references. If downgrade
fails, it restores the verified database dump before restarting the previous services. A failed automatic rollback
prints the exact backup directory for manual recovery and leaves that backup untouched.

## Telemetry

Enabled by default, matching the Mem0 OSS library. Sends at most two events per install to the same anonymous PostHog project the library uses:

- `admin_registered` — fired when the first admin is created (wizard or direct API call). Properties: email domain, server version, install UUID.
- `onboarding_completed` — fired when the setup wizard reaches its final success state. Carries the same properties plus the freeform `use_case` the operator entered. API-only bootstraps never emit this event.

Set `MEM0_TELEMETRY=false` to opt out.

## Security headers

The dashboard sets the following response headers on every path (see `server/dashboard/next.config.mjs`):

- `X-Frame-Options: DENY`
- `Content-Security-Policy: frame-ancestors 'none'`
- `X-Content-Type-Options: nosniff`
- `Referrer-Policy: strict-origin-when-cross-origin`

Together these prevent iframe embedding, sniffing of mislabelled MIME types, and cross-origin referrer leaks. Harden further behind your own reverse proxy if needed.

## Migrating from `ankane/pgvector` to `pgvector/pgvector`

The `ankane/pgvector` Docker image is archived and no longer maintained. This release
replaces it with the official `pgvector/pgvector:pg17` image (PostgreSQL 17, pgvector 0.8.0).

**What changed:**

|                    | Before                          | After                                                    |
| ------------------ | ------------------------------- | -------------------------------------------------------- |
| Docker image       | `ankane/pgvector:v0.5.1`        | `pgvector/pgvector:pg17`                                 |
| PostgreSQL version | 15                              | 17                                                       |
| pgvector version   | 0.5.1                           | 0.8.0                                                    |
| Credentials        | Hardcoded `postgres`/`postgres` | Driven by `POSTGRES_USER` / `POSTGRES_PASSWORD` env vars |

### Fresh installs (no existing data)

No migration needed. Copy `.env.example` to `.env`, set `POSTGRES_PASSWORD`, and run:

```bash
cd server
make up
```

### Existing installs (preserving data)

PostgreSQL 17 cannot read data files written by PostgreSQL 15 directly.
You must export your data first, then import it into the new container.

**1. Export your data from the old container**

With the old stack still running:

```bash
cd server

# Dump all databases (mem0 memories + mem0_app auth/config data)
docker compose exec -T postgres pg_dumpall -U postgres > mem0_backup.sql
```

Verify the dump file is non-empty:

```bash
ls -lh mem0_backup.sql
```

**2. Stop the old stack and remove the old volume**

```bash
# Stop containers
docker compose down

# Remove the old Postgres data volume
docker compose down -v
```

> **Warning:** `docker compose down -v` deletes the `postgres_db` volume permanently.
> Only run this after you have verified your backup.

**3. Update your `.env`**

The Postgres credentials are no longer hardcoded in `docker-compose.yaml`.
Add them to your `.env` file (or verify they match your old setup):

```bash
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=postgres
POSTGRES_USER=postgres
POSTGRES_PASSWORD=<your-password>    # required — compose will refuse to start without it
POSTGRES_COLLECTION_NAME=memories
```

If you previously relied on the hardcoded defaults (`postgres`/`postgres`), set
`POSTGRES_PASSWORD=postgres` to keep the same credentials.

**4. Start only Postgres**

Start **only** the Postgres container first — do not start the mem0 API yet.
The API runs `alembic upgrade head` on startup, which creates empty tables that
would conflict with the restore.

```bash
docker compose up -d postgres
```

Wait for the Postgres healthcheck to pass:

```bash
docker compose exec -T postgres pg_isready -q && echo "ready" || echo "not ready"
```

**5. Restore your data**

```bash
docker compose exec -T postgres psql -U postgres < mem0_backup.sql
```

You may see notices like `role "postgres" already exists` — these are harmless.

> **Important:** You must restore before starting the mem0 API container. The API
> runs database migrations on startup which create empty tables — restoring after
> that would fail with duplicate-key errors and lose your API keys and settings.

**6. Start the API**

Now start the mem0 API container. Alembic will detect the existing tables and
only apply any new migrations:

```bash
docker compose up -d mem0
```

**7. Verify**

```bash
# Check the API is healthy
make health

# Confirm the API-key owner's memories are present
curl -s http://localhost:8888/memories -H "X-API-Key: <your-api-key>"
```

### Rollback

If you need to revert, restore the old image tag in `docker-compose.yaml`:

```yaml
postgres:
  image: ankane/pgvector:v0.5.1
```

Then `docker compose down -v`, `docker compose up -d --build`, and restore from
`mem0_backup.sql` into the old container the same way.

## Reference

Additional product and API documentation lives at [docs.mem0.ai](https://docs.mem0.ai/open-source/overview).
