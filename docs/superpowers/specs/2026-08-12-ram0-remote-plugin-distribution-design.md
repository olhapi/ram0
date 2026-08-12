<!-- SPDX-FileCopyrightText: 2026 Ram0 contributors -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Ram0 Remote Plugin Distribution Design

## Goal

Make Ram0 installation and upgrades work through native Codex and Claude Code
plugin commands without requiring users to clone, pull, or reinstall from the
large Ram0 monorepo.

Normal users add a small remote marketplace once, run `ram0 setup` once, and
then use each host's marketplace/plugin update commands. Local checkout
installation remains a development-only workflow.

## User experience

### Codex

First installation:

```bash
codex plugin marketplace add https://github.com/olhapi/ram0-plugins.git
codex plugin add ram0@ram0-plugins
ram0 setup --url 'https://brain-api.olhapi.com'
ram0 config test
```

Updates:

```bash
codex plugin marketplace upgrade ram0-plugins
```

The implementation must verify whether this Codex command refreshes an already
installed plugin cache. If the installed Codex version requires a second native
plugin command, the help text must state the exact verified command. It must not
prescribe direct cache deletion, local Git operations, or remove/re-add cycles.

### Claude Code

First installation:

```bash
claude plugin marketplace add https://github.com/olhapi/ram0-plugins.git
claude plugin install ram0@ram0-plugins
ram0 setup --url 'https://brain-api.olhapi.com'
ram0 config test
```

Updates:

```bash
claude plugin marketplace update ram0-plugins
claude plugin update ram0@ram0-plugins
```

Restart the host only when its CLI says an update requires restart.

## Distribution repository

Create a dedicated public GitHub repository named `olhapi/ram0-plugins`.
It contains only:

- root marketplace manifests required by Codex and Claude Code;
- the generated `ram0` plugin bundle;
- concise installation, update, verification, migration, and development
  documentation;
- license and attribution files required by the Ram0 fork.

It excludes the SDK monorepo, server, databases, evaluation submodule, unrelated
integrations, development caches, credentials, and local configuration.

The dedicated repository is a generated distribution target. The canonical
plugin sources remain under `integrations/ram0-plugin/` in `olhapi/ram0`.
Changes are never authored independently in the distribution repository.

## Publishing model

Add a deterministic exporter in the Ram0 repository. It accepts an explicit
empty output directory and copies an allowlisted set of plugin files while
preserving executable bits. It fails when:

- the output directory is not empty;
- a required file is missing;
- a symlink escapes the canonical plugin tree;
- an unexpected secret/config file matches the output;
- generated manifests disagree on plugin name or version; or
- generated output differs from a repeat export of the same source revision.

The exporter emits a source manifest containing the Ram0 source commit and
plugin version. Generated content receives the repository's existing Ram0
license and modification notices where the file format supports comments.

Publishing uses a narrowly scoped workflow or release script that:

1. checks out a pinned Ram0 source revision;
2. runs the exporter and all bundle validation;
3. updates the dedicated distribution repository;
4. commits only when generated content changed; and
5. publishes from a protected branch after review.

Any new third-party CI action must be pinned to an immutable commit SHA. CI
workflow changes require explicit implementation approval under the repository
rules.

## First-start bootstrap

The MCP connection must not depend on a previously installed `ram0` command.
The generated plugin bundle starts its bundled stdio adapter directly using a
host-supported plugin-root path. This launch form must be proven in isolated
Codex and Claude Code homes before release.

A trusted lifecycle hook idempotently installs or updates these CLI runtime
files from the active plugin bundle:

- `~/.local/bin/ram0`;
- `~/.local/share/ram0/ram0_cli.py`;
- `~/.local/share/ram0/ram0_config.py`; and
- `~/.local/share/ram0/mcp_stdio_adapter.py`.

The hook:

- compares content before replacing files;
- writes via private temporary files followed by atomic replacement;
- preserves executable and private modes;
- never edits shell profiles;
- never reads, rewrites, or deletes `~/.config/ram0/config.json`;
- never prints the API key; and
- fails open for agent startup while reporting one actionable diagnostic.

The CLI remains useful for direct MCP and manual administration, but full-plugin
MCP startup uses the bundled adapter and therefore works before the hook runs.

## Configuration and security

`ram0 setup` is the only normal flow that creates or updates credentials. The
key and URL remain in `~/.config/ram0/config.json` with directory mode `0700`
and file mode `0600`.

Plugin installation or update must never:

- migrate credentials into host configuration or plugin files;
- overwrite an existing Ram0 configuration;
- persist environment exports;
- accept caller-selected memory ownership;
- download executable code from locations other than the reviewed marketplace
  bundle; or
- execute source from the local development checkout in a normal installation.

The API key remains account-authoritative and is sent only to the configured
Ram0 endpoint.

## Migration

Help pages include a one-time migration from the old local marketplace:

1. record that `~/.config/ram0/config.json` is preserved;
2. remove the old configured marketplace and installed plugin through host
   plugin commands;
3. add the remote `olhapi/ram0-plugins` marketplace;
4. install `ram0@ram0-plugins`;
5. restart if required;
6. run `ram0 config test`; and
7. verify the MCP and bundled skills.

The migration must name `ram0-plugins` consistently and remove all
`mem0-plugins` references from Ram0 installation guidance.

## Documentation

Update:

- `integrations/ram0-plugin/README.md`;
- `docs/integrations/ram0-plugin.mdx`;
- `docs/open-source/ram0-mcp.mdx`; and
- the dedicated distribution repository README.

Each help surface contains separate sections for:

- first installation;
- first credential setup;
- updating;
- verification;
- migration from local or old Mem0 marketplaces; and
- development installation from a checkout.

Normal paths use only host plugin commands plus `ram0 setup` and
`ram0 config test`. Git clone/pull commands appear only in the development
section.

## Verification

Automated tests prove:

- the exporter includes only the allowlist and preserves executable modes;
- two exports from one revision are byte-for-byte identical;
- no secret, config, cache, database, or unrelated monorepo file is present;
- marketplace/plugin names and versions agree;
- the bundle contains all MCP, hook, CLI, and twelve skill entrypoints;
- Codex and Claude Code can add the Git marketplace, install the plugin, and
  start MCP from fresh isolated homes without a preinstalled `ram0` command;
- the lifecycle bootstrap installs the CLI without altering existing config;
- host-native update commands replace an older controlled fixture bundle with a
  newer one;
- OpenCode still discovers the same canonical skills; and
- public help text contains no normal-user local-checkout upgrade path.

A release smoke test runs `ram0 config test` against a controlled endpoint and
confirms credentials never appear in logs, manifests, generated files, or host
configuration.

## Rollout

1. Implement and validate the deterministic distribution bundle locally.
2. Create `olhapi/ram0-plugins` only after the generated output passes review.
3. Push the initial generated bundle and protect its main branch.
4. Test clean Codex and Claude Code installation from the public repository.
5. Update the Ram0 help pages to make the remote marketplace the default.
6. Publish the Ram0 source changes and distribution update together.
7. Migrate existing machines using host plugin commands and verify persistent
   configuration remains intact.

The current workflow-skills feature branch stays intact until this distribution
work is complete and the combined result passes final review.
