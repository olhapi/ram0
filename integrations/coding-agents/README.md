<!-- SPDX-FileCopyrightText: 2026 Ram0 contributors -->
<!-- SPDX-License-Identifier: MIT -->

# Claude Code and Codex

This integration combines the official Supermemory coding-agent hooks with the
self-hosted Ram0 Supermemory gateway:

- hooks inject relevant context and capture completed turns automatically;
- the gateway MCP lets either agent search, save, list, and inspect memory on demand;
- Claude Code and Codex derive the same `repo_<name>__<hash>` container from a
  normalized Git remote, so clones and linked worktrees share project memory;
- the bearer key stays in the process environment and is never written to Git.

## Install after deployment

Node.js 20+, `npx`, current Claude Code, and current Codex must be on `PATH`.
Run the installer with the public API origin, without `/mcp`:

```bash
node integrations/coding-agents/install.mjs \
  --base-url https://brain-api.example.com
```

The installer pins `codex-supermemory` 1.0.17, adds the official
`supermemoryai/claude-supermemory` marketplace plugin, and points Codex's
`supermemory` MCP registration directly at the local gateway. It writes only a
non-secret environment helper and an installation marker under
`~/.config/ram0-supermemory/`.

Put the gateway key in your preferred secret-backed shell setup, then source the
generated helper before starting either client:

```bash
export SUPERMEMORY_API_KEY='the-key-from-the-Unraid-runtime-env'
source ~/.config/ram0-supermemory/env.sh
```

The helper maps the one key into the environment names expected by the upstream
Claude Code and Codex hooks. It also sends their automatic REST traffic to the
gateway and their MCP traffic to `<base-url>/mcp`. Do not put the key in a repo,
plugin config, or command-line argument.

Run the installer again with the same URL to make no changes. Use `--force` to
refresh the plugin installations or repair their registrations.

## Existing Ram0 workstations

To migrate an existing workstation to the public Ram0 repository, use a new
checkout and install the gateway integration there:

```bash
git clone https://github.com/olhapi/ram0.git ~/projects/ram0-supermemory
cd ~/projects/ram0-supermemory
node integrations/coding-agents/install.mjs \
  --base-url https://brain-api.olhapi.com \
  --force
source ~/.config/ram0-supermemory/activate.sh
```

Securely transfer or recreate the mode-`0600`
`~/.config/ram0-supermemory/credentials.env` file; never place its key in a
command-line argument. Keep the old checkout and its configuration untouched
until the new installation's status check succeeds. Only then remove the old
`ram0@ram0-plugins` marketplace entry. Restart Claude Code and Codex from a
shell where `activate.sh` has been sourced so both clients inherit the new
credentials and gateway configuration.

## Verify

Restart both clients after installation.

In Codex, run `/mcp`; `supermemory` should be connected. In Claude Code, run
`/mcp` and `/supermemory:status`. Then ask either agent:

```text
Search memory for the last deployment decision before answering.
```

The MCP tools are `search_memory`, `add_memory`, `list_memories`,
`list_documents`, `get_document`, `list_spaces`, and `who_am_i`. Automatic
hooks remain best-effort: a memory outage must not prevent coding work, while an
explicit MCP call reports the failure.

To inspect the shared repository scope directly:

```bash
node integrations/coding-agents/project-scope.mjs /path/to/repository
```

## Security notes

- HTTPS is expected for any non-loopback URL.
- The MCP gateway requires bearer authentication on every request.
- The installer rejects URLs containing credentials, queries, or fragments.
- Content inside `<private>...</private>` is redacted by the upstream hooks.
- Review plugin updates before using `--force`; the Claude marketplace follows
  its upstream repository while the Codex package is version-pinned here.

Codex's native Streamable HTTP and bearer-token configuration is documented in
the [official Codex MCP guide](https://developers.openai.com/codex/mcp). Claude
Code's MCP environment expansion and user scopes are documented in the
[official Claude Code MCP guide](https://docs.anthropic.com/en/docs/claude-code/mcp).
