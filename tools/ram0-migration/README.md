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

The journal is append-only. A retry skips acknowledged deterministic keys. A
failed batch is reported but never journaled, so it remains eligible on the next
run. Planner output and new journal files are mode 600.

`verify` compares the expected record count in every target container with the
v4 API's current count. The guarded deployment retries this while the engine
settles and will not promote an incomplete import.
