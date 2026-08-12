<!-- Modified for Ram0; see NOTICE and repository history. -->

# Ram0

Ram0 is a self-hosted memory service for assistants and coding agents. It adds
account-derived isolation, API-key authentication, custom memory categories,
an authenticated MCP endpoint, and a local administration dashboard to a
maintainable fork of the Mem0 open-source project.

> Ram0 is an independent fork and is not affiliated with or endorsed by Mem0.
> Mem0 is referenced to identify the upstream project and software origin.

## What Ram0 adds

- Account-scoped memory ownership derived from authenticated credentials
- Dashboard users, invitations, session authentication, and API keys
- Custom categories with asynchronous classification and reclassification
- A bearer-authenticated Streamable HTTP MCP endpoint at `/mcp`
- A Ram0 plugin for Claude Code, Codex, Cursor, and OpenCode
- Immutable GHCR images and guarded self-hosted deployment tooling
- PostgreSQL/pgvector persistence with Alembic migrations

Ram0 keeps these additions concentrated around the self-hosted server,
dashboard, and integration seams so upstream changes remain practical to
adopt. It does not claim to add Ram0-specific APIs to the upstream Python or
TypeScript SDKs or to the hosted Mem0 Platform.

## Quick start

Requirements: Docker with Compose, an LLM provider key, and a strong database
password.

```bash
git clone https://github.com/olhapi/ram0.git
cd ram0/server
cp .env.example .env
# Set POSTGRES_PASSWORD, JWT_SECRET, and the required model-provider keys.
docker compose up --build
```

The default development endpoints are:

- Dashboard: `http://localhost:3000`
- REST/OpenAPI: `http://localhost:8888/docs`
- PostgreSQL: `localhost:8432` for development only

Authentication is enabled by default. Follow the dashboard setup flow to
create the first administrator. `AUTH_DISABLED=true` is intended only for
isolated local development.

See [the self-hosted server guide](./server/README.md) for configuration,
upgrades, backups, migrations, and deployment details.

## Memory ownership

Every authenticated account owns one isolated memory namespace. Ram0 derives
the owner UUID from the session or API key; clients do not choose a `user_id`.
The same policy is applied across memory CRUD, search, entities, categories,
jobs, and MCP tools.

This differs from ordinary Mem0 OSS entity parameters, which are useful for
application-level grouping but are not, by themselves, an authorization
boundary.

## Custom categories

Administrators define a category catalogue in the dashboard or REST API.
Ram0 classifies newly added and text-updated memories asynchronously and can
preview or run bounded reclassification jobs. Classification failure does not
discard the memory.

The API and operator contract is documented in
[Custom categories](./docs/open-source/features/rest-api.mdx#custom-categories-ram0).

## MCP and coding-agent plugin

Ram0 exposes six account-scoped tools through an authenticated `/mcp`
endpoint. The API key selects the account; MCP callers never provide a
`user_id`.

```bash
python3 integrations/ram0-plugin/scripts/install_cli.py
ram0 setup --url 'https://ram0.example.com'
ram0 config test
codex mcp add ram0 -- python3 ~/.local/share/ram0/mcp_stdio_adapter.py
```

Use one integration path per client: direct MCP for tools only, or the full
Ram0 plugin for tools plus lifecycle retrieval and bounded durable capture.

- [Ram0 MCP guide](./docs/open-source/ram0-mcp.mdx)
- [Ram0 plugin guide](./docs/integrations/ram0-plugin.mdx)

## Container images

The repository publishes separate API and dashboard images to GitHub
Container Registry using immutable SHA tags:

- `ghcr.io/olhapi/ram0-api:sha-<git-sha>`
- `ghcr.io/olhapi/ram0-dashboard:sha-<git-sha>`

Production deployments should resolve and pin image digests. The guarded
Unraid procedure, backup requirements, and rollback behavior are documented in
[the server guide](./server/README.md#unraid-deployment).

## Development

This is a polyglot upstream-compatible repository. Use the package-specific
commands documented in [AGENTS.md](./AGENTS.md): Hatch/pytest and Ruff for
Python, and pnpm with the package's configured checker for TypeScript.

For dashboard work:

```bash
pnpm -C server/dashboard install
pnpm -C server/dashboard typecheck
pnpm -C server/dashboard lint
pnpm -C server/dashboard build
```

## Upstream Mem0

Ram0 is derived from [Mem0](https://github.com/mem0ai/mem0), initially based
on upstream release [`v2.0.17`](https://github.com/mem0ai/mem0/tree/v2.0.17).
Mem0 provides the underlying open-source memory SDK and provider ecosystem.
For upstream SDK APIs, providers, research, and hosted-platform documentation,
consult [Mem0's documentation](https://docs.mem0.ai).

Ram0 retains upstream attribution and aims to keep its policy and deployment
changes narrow enough for regular upstream integration.

## License and notices

Ram0 and the inherited Mem0 source are distributed under the
[Apache License 2.0](./LICENSE). Fork provenance and attribution are recorded
in [NOTICE](./NOTICE).

The dashboard bundles free and open-source fonts whose redistribution terms
and copyright notices are provided in
[the font notice index](./server/dashboard/public/legal/fonts/README.md).

The Apache License does not grant rights to third-party trademarks. Product
and company names used in technical documentation belong to their respective
owners.
