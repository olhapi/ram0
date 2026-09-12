<!-- SPDX-FileCopyrightText: 2026 Ram0 contributors -->
<!-- SPDX-License-Identifier: MIT -->

# Ram0 memory migration

The migration CLI converts bounded Ram0 REST exports into direct Supermemory v4
memory imports. It is deterministic, exact-deduplicating, and resumable.

Input is a JSON array with one object per account:

```json
[
  {
    "account": { "id": "account-id" },
    "memories": { "results": [{ "id": "memory-id", "memory": "text" }] }
  }
]
```

Use `plan`, inspect aggregate `stats`, then use `import` with the API key in the
environment:

```bash
bun src/cli.ts plan --exports exports.json --mapping mapping.json --output import-plan.json
bun src/cli.ts stats --plan import-plan.json
SUPERMEMORY_API_KEY=... bun src/cli.ts import \
  --plan import-plan.json \
  --journal import-journal.jsonl \
  --base-url http://127.0.0.1:18888

SUPERMEMORY_API_KEY=... bun src/cli.ts verify \
  --plan import-plan.json \
  --base-url http://127.0.0.1:18888
```

The journal is append-only and is not the destination authority. Every run lists
the target containers and reconciles `ram0_migration_key` identities with exact
source content and provenance. Lost responses, partial commits, and failed
journal appends are reconciled on resume; only missing records are sent. An
ambiguous duplicate identity, conflicting provenance, or journal record absent
from the destination fails closed for manual inspection. Do not delete journals
to bypass this check. Old imports without identity metadata require manual
reconciliation, not a blind rerun. Planner output and new journals are mode 600.

Run exactly one importer against a quiescent destination, with no concurrent
agent writes. Reconciliation uses complete direct-memory listings, not the
eventually indexed search API. If the destination cannot provide authoritative
listings, stop and reconcile it before retrying; do not infer missing writes from
search results or counts. After a transport failure, wait for all in-flight
engine writes to settle before retrying; a still-running request is another
writer. Never delete or rewrite the source export/journal.

`verify` reports the number of uniquely covered plan records in each container;
duplicates, unrelated rows, changed text, and missing provenance do not count.
The guarded deployment retries coverage checks and cannot promote an incomplete
import. Before using import for cutover, follow the final write-freeze/export
boundary in [the deployment runbook](../../deploy/supermemory/README.md).
