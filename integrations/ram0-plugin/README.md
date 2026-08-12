<!-- Modified for Ram0; see NOTICE and repository history. -->

# Ram0 agent plugin

The Ram0 plugin gives Claude Code, Codex, Cursor, and OpenCode six account-scoped MCP tools plus automatic retrieval, bounded durable-memory capture, and 12 workflow skills. Direct MCP is tools-only; skills-only adds guidance. Choose one MCP registration per client.

## Install

Use the generated remote marketplace. A Ram0 source checkout is not required.

Claude Code:

```bash
claude plugin marketplace add https://github.com/olhapi/ram0-plugins.git
claude plugin install ram0@ram0-plugins
```

Codex:

```bash
codex plugin marketplace add https://github.com/olhapi/ram0-plugins.git
codex plugin add ram0@ram0-plugins
```

Restart the client. In Codex, open `/hooks`, review the bundled lifecycle hooks, and trust them; approve the corresponding hooks when Claude Code prompts. The trusted session-start hook installs or refreshes the bundled `ram0` CLI atomically. The bundled MCP adapter starts directly from the plugin, so it does not depend on `ram0` already being on `PATH`.

Create an account-owned key on the dashboard **API Keys** page; it is shown once. Then configure and verify Ram0:

```bash
ram0 setup --url 'https://ram0.example.lan'
ram0 config test
```

`ram0 setup` reads the key without echo and stores exactly `api_url` and `api_key` in `~/.config/ram0/config.json`. The directory is `0700` and the file is `0600`. Plugin installation and upgrades preserve this configuration. The adapter sends the key only as `Authorization: Bearer` to that endpoint; it is never stored as memory or logged, and never sent to telemetry or third parties.

Non-empty `RAM0_API_URL` and `RAM0_API_KEY` override individual stored fields only for explicitly managed development or CI processes. Normal installation does not require exports or shell-profile changes.

## Update

Codex refreshes the Git marketplace and installed plugin snapshot in one command:

```bash
codex plugin marketplace upgrade ram0-plugins
```

Claude Code refreshes the marketplace, then the installed plugin:

```bash
claude plugin marketplace update ram0-plugins
claude plugin update ram0@ram0-plugins
```

Restart the client after an upgrade. These commands preserve `~/.config/ram0/config.json`; no checkout, cache deletion, or plugin removal is part of a normal update.

## Verify

Run `ram0 config test`, confirm the `ram0` MCP starts without an environment-variable warning, and invoke `health`. To verify persistence end to end, call `remember`, start a new task, then use `search_memories`.

## Workflow skills

- Write: `remember`
- Browse: `peek`, `tour`
- Delete: `forget` (confirmation required)
- Portable data: `export`, `import` (bounded scan and reviewed batch)
- Quality: `memory-reviewer` (read-only), `dream` (confirmed consolidation), `stats`
- Setup: `health`, `onboard`
- Policy: `ram0-memory`

Plugin installation includes all skills automatically. OpenCode discovers all 12 bundled skills from the installed plugin without copying them into user configuration. The `npx skills add ... --skill ram0-memory` command installs only the standalone policy skill.

## Direct MCP and skills-only

Direct MCP runs the stable config-aware stdio bridge:

```bash
codex mcp add ram0 -- python3 ~/.local/share/ram0/mcp_stdio_adapter.py
claude mcp add ram0 --scope user -- python3 ~/.local/share/ram0/mcp_stdio_adapter.py
```

For guidance without automation:

```bash
npx skills add https://github.com/olhapi/ram0 --skill ram0-memory
```

The plugin never sends or saves raw prompts, raw transcripts, file dumps, complete source/code/diff content, credentials, or local identities. It captures only bounded durable candidates and fails open when configuration or the endpoint is unavailable.

## Migration

If `ram0-plugins` currently points at a local marketplace, convert it once. This replacement is only for migration; future upgrades use the commands above. Your Ram0 config is preserved.

Codex:

```bash
codex plugin remove ram0@ram0-plugins
codex plugin marketplace remove ram0-plugins
codex plugin marketplace add https://github.com/olhapi/ram0-plugins.git
codex plugin add ram0@ram0-plugins
```

Claude Code:

```bash
claude plugin uninstall ram0@ram0-plugins
claude plugin marketplace remove ram0-plugins
claude plugin marketplace add https://github.com/olhapi/ram0-plugins.git
claude plugin install ram0@ram0-plugins
```

Also remove any duplicate direct Ram0 MCP registration before enabling the full plugin.

## Troubleshooting

- Rotate a key with `ram0 config set-key`.
- Repair unsafe permissions with `chmod 600 ~/.config/ram0/config.json`.
- For missing configuration, rerun `ram0 setup`. For an unreachable endpoint, use `ram0 config show`, then `ram0 config test`.

Categories are private to the API-key owner. The server derives owner identity from the Bearer key and rejects caller-owned identity fields.

## Development

Contributors who need to modify the plugin can use a local checkout. This is not the normal install or update path:

```bash
git clone https://github.com/olhapi/ram0.git ~/ram0-development/ram0
python3 ~/ram0-development/ram0/integrations/ram0-plugin/scripts/install_cli.py
```

Cursor and OpenCode development installation remains checkout-based; see the client manifests and `.opencode-plugin` package in this directory. See [UPSTREAM.md](UPSTREAM.md) for the adaptation boundary and update procedure.
