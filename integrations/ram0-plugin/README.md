# Ram0 agent plugin

The Ram0 plugin gives Claude Code, Codex, Cursor, and OpenCode six account-scoped MCP tools plus automatic retrieval and bounded durable-memory capture. Direct MCP is tools-only; skills-only adds guidance. Choose one MCP registration per client.

## Permanent setup

Create an account-owned key on the dashboard **API Keys** page; it is shown once. Review this checkout, then install the local configuration CLI:

```bash
git clone https://github.com/olhapi/ram0.git ~/ram0-plugins/ram0
python3 ~/ram0-plugins/ram0/integrations/ram0-plugin/scripts/install_cli.py
ram0 setup --url 'https://ram0.example.lan'
ram0 config test
```

`ram0 setup` reads the key without echo and stores exactly `api_url` and `api_key` in `~/.config/ram0/config.json`. The directory is `0700` and the file is `0600`. The adapter sends the key only as `Authorization: Bearer` to that endpoint. It is never stored as memory or logged, and never sent to telemetry or third parties.

Non-empty `RAM0_API_URL` and `RAM0_API_KEY` override individual stored fields only for explicitly managed development or CI processes. Normal installation does not require exports or shell-profile changes.

## Full automation plugin

Claude Code:

```bash
claude plugin marketplace add https://github.com/olhapi/ram0.git
claude plugin install ram0@ram0-plugins
```

Codex:

```bash
codex plugin marketplace add ~/ram0-plugins/ram0
codex plugin add ram0@ram0-plugins
```

Restart Codex, open `/hooks`, review the bundled lifecycle hooks, and trust them. Cursor users add `~/ram0-plugins/ram0/.cursor-plugin/marketplace.json` under **Settings → Plugins → Add Marketplace**, install Ram0, and reload Cursor.

OpenCode:

```bash
cd ~/ram0-plugins/ram0/integrations/ram0-plugin/.opencode-plugin
bun install --frozen-lockfile && bun run build
opencode plugin "file://$PWD" --global
```

Restart the client after installation. The full plugin already registers MCP; remove any same-named direct remote MCP entry.

## Direct MCP and skills-only

Direct MCP runs the stable config-aware stdio bridge:

```bash
codex mcp add ram0 -- python3 ~/.local/share/ram0/mcp_stdio_adapter.py
claude mcp add ram0 --scope user -- python3 ~/.local/share/ram0/mcp_stdio_adapter.py
```

Cursor and OpenCode can configure the same `python3` command and adapter path in their local stdio MCP JSON. For guidance without automation:

```bash
npx skills add https://github.com/olhapi/ram0 --skill ram0-memory
```

The skill searches before writing. The plugin never sends or saves raw prompts, raw transcripts, file dumps, complete source/code/diff content, credentials, or local identities. It captures only bounded durable candidates and fails open when configuration or the endpoint is unavailable.

## Migration and troubleshooting

- Remove the old `mem0-plugins` marketplace and any duplicate remote Ram0 MCP entry, then install `ram0@ram0-plugins`.
- Rotate a key with `ram0 config set-key`; earlier proof-bound automatic context becomes explicitly searchable but is not deleted.
- Repair unsafe permissions with `chmod 600 ~/.config/ram0/config.json`.
- For missing configuration, rerun `ram0 setup`. For an unreachable endpoint, use `ram0 config show`, then `ram0 config test`.

Categories are private to the API-key owner. First access copies the legacy template; onboarding appends missing coding categories without overwriting edits. The server derives owner identity from the Bearer key and rejects caller-owned identity fields.

The upstream `integrations/mem0-plugin` directory remains unchanged. See [UPSTREAM.md](UPSTREAM.md) for the adaptation boundary and update procedure.
