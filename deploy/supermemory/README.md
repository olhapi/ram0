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
  import-plan.json
  import-journal.jsonl   # created by the importer
```

`import-plan.json` must contain both Ram0 account IDs, zero invalid records, and
balanced source counts. The deploy script refuses promotion otherwise. Preserve
the raw two-account export separately even though the normalized plan is itself
sufficient to replay every accepted memory.

Copy `.env.example` to `supermemory.env`, set the host IP, OpenAI key, legacy
Compose paths, public Cloudflare origins, full Git revision, and three immutable
`@sha256` image references. Never commit this file or the generated engine key.

## Build the import plan

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

1. validates immutable images and the two-account plan;
2. creates and validates a custom-format PostgreSQL dump;
3. starts Supermemory on candidate ports `127.0.0.1:28888` and
   `127.0.0.1:23000`;
4. imports through the journaled importer and verifies every record is covered;
5. stops only `ram0_api` and `ram0_dashboard`;
6. starts the replacement on ports `18888` and `13000` and verifies REST, MCP,
   the graph UI, and configured Cloudflare origins.

If a post-stop gate fails, the script stops the new application containers and
restarts the old Compose services. It never removes a volume or deletes the old
PostgreSQL/history directories. The old `ram0_postgres` remains available for
rollback until you deliberately retire it in a separate maintenance window.
