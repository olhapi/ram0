# SPDX-FileCopyrightText: 2026 Ram0 contributors
# SPDX-License-Identifier: Apache-2.0
"""Public documentation contracts for remote Ram0 plugin distribution."""

from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[3]
PAGES = (
    REPOSITORY / "integrations/ram0-plugin/README.md",
    REPOSITORY / "docs/integrations/ram0-plugin.mdx",
    REPOSITORY / "docs/open-source/ram0-mcp.mdx",
)

REMOTE = "https://github.com/olhapi/ram0-plugins.git"
CODEX_UPDATE = "codex plugin marketplace upgrade ram0-plugins"
CLAUDE_MARKETPLACE_UPDATE = "claude plugin marketplace update ram0-plugins"
CLAUDE_PLUGIN_UPDATE = "claude plugin update ram0@ram0-plugins"


def test_public_docs_use_remote_native_install_and_update_commands():
    for page in PAGES:
        text = page.read_text()
        assert REMOTE in text, page
        assert "codex plugin marketplace add " + REMOTE in text, page
        assert "claude plugin marketplace add " + REMOTE in text, page
        assert "ram0@ram0-plugins" in text, page
        assert CODEX_UPDATE in text, page
        assert CLAUDE_MARKETPLACE_UPDATE in text, page
        assert CLAUDE_PLUGIN_UPDATE in text, page
        assert "mem0-plugins" not in text, page


def test_public_docs_cover_setup_verification_preservation_and_migration():
    for page in PAGES:
        text = page.read_text()
        lowered = text.lower()
        assert "ram0 setup" in text, page
        assert "ram0 config test" in text, page
        assert "preserv" in lowered and "config" in lowered, page
        assert "## Migration" in text, page
        assert "local marketplace" in lowered, page
        assert "## Development" in text, page


def test_checkout_commands_are_development_only():
    for page in PAGES:
        text = page.read_text()
        normal, development = text.split("## Development", 1)
        for checkout_command in ("git clone", "git pull", "install_cli.py"):
            assert checkout_command not in normal, (page, checkout_command)
        assert "git clone" in development, page
