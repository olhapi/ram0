<!-- SPDX-FileCopyrightText: 2026 Ram0 contributors -->
<!-- SPDX-License-Identifier: MIT -->

# Unraid cutover runbook

This package is prepared for a later, explicit cutover. Running tests or building
images does not change the current Ram0 deployment.

## Required private inputs

Create these on Unraid beneath `/mnt/user/appdata/mem0/` and keep them mode 600
inside mode 700 directories:

```text
supermemory.env
migration/
  final-exports.json    # immutable final two-account export after write freeze
  import-plan.json
  import-journal.jsonl   # created by the importer
  write-freeze.json     # operator attestation, bound to stopped writers and hashes
```

`import-plan.json` must contain both Ram0 account IDs, zero invalid records, and
balanced source counts. The deploy script refuses promotion otherwise. Preserve
the raw two-account export separately even though the normalized plan is itself
sufficient to replay every accepted memory.

Copy `.env.example` to `supermemory.env`, set the host IP, OpenAI key, legacy
Compose paths, public Cloudflare origins, full Git revision, and three immutable
`@sha256` image references. Never commit this file or the generated engine key.

## Rehearse the import plan

Combine both account exports into one JSON array and provide an explicit mapping
for every non-empty Ram0 `app_id`:

```json
{
  "personalContainer": "personal",
  "appIds": {
    "github.com-owner-repository": "repo_repository__0123456789abcdef"
  }
}
```

From this checkout:

```bash
bun tools/ram0-migration/src/cli.ts plan \
  --exports /private/exports.json \
  --mapping /private/container-mapping.json \
  --output /mnt/user/appdata/mem0/migration/import-plan.json

bun tools/ram0-migration/src/cli.ts stats \
  --plan /mnt/user/appdata/mem0/migration/import-plan.json
```

The planner trims empty text, rejects records over the local API limit, merges
only exact same-container duplicates, and preserves source IDs, account IDs,
timestamps, categories, and safe scalar metadata under `ram0_*` keys.

## Required final write-freeze boundary

Rehearsal exports are not cutover evidence. In the explicitly authorized cutover
window, stop all legacy writers: API, dashboard, agent hooks, scheduled jobs,
and any direct database writers. Keep them stopped until promotion or a deliberate
rollback. In particular, `ram0_api` and `ram0_dashboard` must be stopped, not
merely hidden behind ingress. The deployment script refuses to run otherwise.

After writers have stopped, obtain a complete final two-account export using a
trusted read-only database export procedure (the REST API is now stopped).
Verify source accounting against that frozen database, preserve the raw export
as `final-exports.json`, and generate a new `import-plan.json` from it. Do not
overwrite rehearsal exports, prior plans, or journals: use a new private migration
directory for each freeze generation. The supplied CLI normalizes an export; it
does not itself export the legacy database or prove operator attestations.

Create mode-600 `write-freeze.json` in that same private directory with this
schema, replacing placeholders using the actual stopped containers' `.State.FinishedAt`
values from `docker inspect` and SHA-256 hashes of the final files:

```json
{
  "version": 1,
  "otherWritersStopped": true,
  "apiFinishedAt": "<ram0_api stopped timestamp>",
  "dashboardFinishedAt": "<ram0_dashboard stopped timestamp>",
  "exportSha256": "<final-exports.json SHA-256>",
  "planSha256": "<import-plan.json SHA-256>"
}
```

Setting `otherWritersStopped` attests that external jobs/direct writers are also
disabled and this is the final export taken after the freeze, not a rehearsal.
Preflight, import, and promotion independently recheck stopped container state,
matching lifecycle timestamps, and both file hashes. No other destination writer
or importer may run during migration. Do not run the CLI manually to bypass these
deployment gates.

## Validate without deploying

```bash
bash deploy/supermemory/tests/verify-compose.sh
bash deploy/supermemory/deploy-unraid.sh --self-test
bash -n deploy/supermemory/deploy-unraid.sh deploy/supermemory/verify-stack.sh

RAM0_RUNTIME_ENV_FILE="$PWD/deploy/supermemory/.env.example" \
  docker compose --env-file deploy/supermemory/.env.example \
  -f deploy/supermemory/compose.yaml config --quiet
```

## Cut over later

On Unraid, from the deployed checkout:

```bash
sudo env RAM0_DEPLOY_ENV=/mnt/user/appdata/mem0/supermemory.env \
  bash deploy/supermemory/deploy-unraid.sh
```

The script:

1. validates the final frozen-export evidence, immutable images, and two-account plan;
2. creates and validates a custom-format PostgreSQL dump;
3. starts Supermemory on candidate ports `127.0.0.1:28888` and
   `127.0.0.1:23000`;
4. imports through the journaled importer and verifies every record is covered;
5. rechecks that legacy writers remain stopped at the attested freeze boundary;
6. starts the replacement on ports `18888` and `13000` and verifies REST, MCP,
   the graph UI, and configured Cloudflare origins.

If a post-stop gate fails, the script stops the new application containers and
restarts the old Compose services. It never removes a volume or deletes the old
PostgreSQL/history directories. The old `ram0_postgres` remains available for
rollback until you deliberately retire it in a separate maintenance window.

Failures before promotion leave legacy writers frozen: repair and resume with
the same immutable export/plan and append-only journal, or deliberately resume
legacy service using the saved Compose configuration. A promotion rollback
restarts the legacy services, invalidating the freeze evidence. After any legacy
resume, take a new freeze and final export before retrying; old plans are stale.
Preserve the previous export, plan, journal, and candidate data for reconciliation.
If new destination writes were accepted before rollback, preserve/export those
too and reconcile both sides before another promotion—never discard them or
blindly reuse a candidate with superseded memories.

## Upgrade the engine

Engine 0.0.6 cannot load its workflow runtime (`@rivetkit/rivetkit-wasm`), so it
accepts documents but never processes them; they fail after four hours in
`queued`. 0.0.7 and later process documents and open 0.0.6 stores in place.
Since 0.0.7 the self-hosted engine is licensed for up to 10,000 documents.

On Unraid, as root, from a checkout at the new revision. First set
`RAM0_ENGINE_IMAGE` and `RAM0_REVISION` in `supermemory.env` to the new immutable
digest and revision. Then stop the engine, back up its store, and recreate only
the engine:

```bash
env_file=/mnt/user/appdata/mem0/supermemory.env
set -a; . "$env_file"; set +a
compose() { docker compose --env-file "$env_file" -f deploy/supermemory/compose.yaml "$@"; }
compose stop engine
cp -a "$RAM0_SUPERMEMORY_DATA_DIR" "$RAM0_SUPERMEMORY_DATA_DIR.backup-$(date +%Y%m%d%H%M%S)"
compose up -d --no-deps --force-recreate engine
bash deploy/supermemory/verify-stack.sh \
  "http://$RAM0_HOST_IP:18888" "http://$RAM0_HOST_IP:13000" "$RAM0_RUNTIME_ENV_FILE" \
  "${RAM0_PUBLIC_API_URL:-}" "${RAM0_PUBLIC_GRAPH_URL:-}"
```

`verify-stack.sh` adds a document to `ram0_deploy_check` and passes only when
extraction finishes as `done`; a missing or invalid `OPENAI_API_KEY` fails it.
To roll back, stop the engine, restore the backup directory, and restore the
previous digest: an upgraded store may not open in the older engine.

The engine retries only some failed documents. After a successful upgrade,
re-queue documents that failed under the broken engine from a workstation with
the gateway key loaded. Only documents with a `customId` are re-posted, in place:

```bash
node deploy/supermemory/requeue-failed-documents.mjs --base-url https://brain-api.olhapi.com
node deploy/supermemory/requeue-failed-documents.mjs --base-url https://brain-api.olhapi.com --apply
```
