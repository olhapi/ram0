# Dashboard Help Public Marketplace Design

## Goal

Make the dashboard Help page an end-user installation guide for the published
Ram0 plugin. A user must not need a Ram0 source checkout to install or update
the plugin.

## Installation contract

- Help uses `https://github.com/olhapi/ram0-plugins.git` as the marketplace
  source for supported plugin clients.
- Help separates fresh installation from updating an existing installation.
- Help contains no repository clone, local-checkout marketplace, contributor,
  source-build, or local OpenCode build instructions.
- Persistent Ram0 configuration remains in `~/.config/ram0/config.json`; the
  page must not expose credentials or recommend transient environment exports.
- Contributor and development setup remains in repository README files only.

## Product naming

- The section title remains **Using Ram0** because it describes this product.
- Links whose destination is the Mem0 documentation site must identify the
  destination as Mem0. In particular, the existing `docs.mem0.ai` link is
  labeled **Mem0 MCP guide**, not **Ram0 MCP guide**.
- Ram0-owned commands, plugin identifiers, configuration, and UI copy continue
  to use the Ram0 name.

## Client presentation

Codex and Claude Code show native public-marketplace install and update
commands. Cursor and OpenCode must not fall back to source-checkout directions;
if no stable public install command is supported for a client, Help states that
clearly and points users to a supported public distribution path without
inventing a local build workflow.

## Verification

Tests assert the public marketplace URL and native install/update commands.
They reject `git clone`, local checkout paths, contributor language, and local
OpenCode build commands anywhere in Help content. A rendering-source contract
keeps **Using Ram0** and requires **Mem0 MCP guide** for the external Mem0 link.
