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
