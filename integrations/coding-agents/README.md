<!-- SPDX-FileCopyrightText: 2026 Ram0 contributors -->
<!-- SPDX-License-Identifier: MIT -->

# Claude Code and Codex

This integration combines the official Supermemory coding-agent hooks with the
self-hosted Ram0 Supermemory gateway:

- a Ram0 read-only hook recalls `personal` and the derived repository container
  at session start and on each prompt; upstream hooks retain automatic capture;
- the gateway MCP lets either agent search, save, list, and inspect memory on demand;
- Claude Code and Codex derive the same `repo_<name>__<hash>` container from a
  normalized Git remote, so clones and linked worktrees share project memory;
- the bearer key is stored only in the upstream plugins' private credential
  files, never in Git or client settings, so clients launched from a desktop
  app or service work without a shell profile.

## Install after deployment

Node.js 20+, `npx`, current Claude Code, and current Codex must be on `PATH`.
Run the installer with the public API origin, without `/mcp`:

```bash
node integrations/coding-agents/install.mjs \
  --base-url https://brain-api.example.com
```

The installer pins `codex-supermemory` 1.0.17 and adds the official
`supermemoryai/claude-supermemory` marketplace plugin. It keeps Codex's upstream
`supermemory` MCP proxy and sets only its `SUPERMEMORY_MCP_URL`. It writes a
non-secret environment helper, recall adapter, scope helper, and an installation
marker under `~/.config/ram0-supermemory/`. It merges additive hook registrations
into `~/.codex/hooks.json` and `~/.claude/settings.json`, preserving existing
hooks and settings. It does not redirect repository writes into `personal`.

Put the gateway key in a separate credentials file using your preferred
secret-backed local editor. The credentials file must export
`SUPERMEMORY_API_KEY`; the installer neither creates nor sources it. Source it
before running the installer.

Desktop apps and services do not read shell profiles, so the installer does not
rely on them. It adds the non-secret gateway REST and MCP URLs to the `env`
block of `~/.claude/settings.json`. Codex reads the REST URL from its credential
file. The installer then stores the key from `SUPERMEMORY_API_KEY` in the files
the upstream login would write: `~/.supermemory-claude/credentials.json` and
`~/.codex/supermemory/credentials.json` (mode `0600`). With a stored key, the
plugins never open the hosted Supermemory login. Do not put the key in a repo,
client settings, or command-line argument.

The generated helper still maps the key into the upstream environment names for
shells that source it; environment variables take precedence over the files.

Run the installer again with the same URL to make no changes; a rotated key is
the only thing it refreshes. Use `--force` to refresh the plugin installations or
repair their registrations.

## Existing Ram0 workstations

To migrate an existing workstation to the public Ram0 repository, retain the
old checkout. Before using `--force`, make a separate mode-`0700` backup of any
existing integration configuration. This prevents the installer from replacing
the only copy of its generated configuration. The backup is fail closed: a
timestamp collision or any backup error stops the sequence. Do not continue
unless the backup completes:

```bash
config_dir="$HOME/.config/ram0-supermemory"
backup_root="$HOME/.config/ram0-supermemory-backup-$(date +%Y%m%d%H%M%S)"
if [ -d "$config_dir" ]; then
  if ! (
    umask 077
    mkdir "$backup_root" &&
      chmod 700 "$backup_root" &&
      mkdir "$backup_root/config" &&
      cp -pR "$config_dir"/. "$backup_root/config"/ &&
      chmod 700 "$backup_root" "$backup_root/config"
  ); then
    printf '%s\n' 'Ram0 configuration backup failed; do not continue.' >&2
    exit 1
  fi
fi
```

Keep that backup and the old checkout until the new clients have passed their
status checks. Use a trusted local editor to securely create or update
`$HOME/.config/ram0-supermemory/credentials.env` with an
`export SUPERMEMORY_API_KEY=...` assignment. Do not place the key in a command
line or shell history. Ensure the credentials file is mode `0600` before
installing:

```bash
config_dir="$HOME/.config/ram0-supermemory"
credentials_file="$config_dir/credentials.env"
mkdir -p "$config_dir"
chmod 700 "$config_dir"
touch "$credentials_file"
chmod 600 "$credentials_file"
```

After saving the credentials file, use a new checkout, source the user-managed
credentials file, and run the installer:

```bash
git clone https://github.com/olhapi/ram0.git "$HOME/projects/ram0-supermemory"
cd "$HOME/projects/ram0-supermemory"
. "$credentials_file"
node integrations/coding-agents/install.mjs \
  --base-url https://brain-api.olhapi.com \
  --force
```

Restart Claude Code and Codex, including any desktop app or service that
launches them. In each client,
run `/mcp` and confirm `supermemory` is connected; in Claude Code, also run
`/supermemory:status`. Only after those checks pass may you remove the legacy
`ram0@ram0-plugins` marketplace entry.

## Verify

Restart both clients after installation.

In Codex, review and trust the new hook definitions when prompted; untrusted
hooks are skipped. See the [official hook trust documentation](https://learn.chatgpt.com/docs/hooks).
The adapter reads two explicit scopes only, redacts `<private>` prompt spans,
and treats failures as best-effort without logging memory or credentials.
Existing upstream hooks remain enabled; their legacy compatibility reads are
separate from the adapter's deliberately narrow scope.

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
