"""Tests for local-only Ram0 plugin settings and marketplace registration."""

from __future__ import annotations

import json
from pathlib import Path

from ram0_settings import DEFAULT_RAM0_API_URL, load_settings


ROOT = Path(__file__).resolve().parents[3]
MARKETPLACES = (
    ROOT / "marketplace.json",
    ROOT / ".agents/plugins/marketplace.json",
    ROOT / ".claude-plugin/marketplace.json",
    ROOT / ".codex-plugin/marketplace.json",
    ROOT / ".cursor-plugin/marketplace.json",
)


def test_defaults_are_local_enabled_and_safe_to_display(tmp_path):
    """Breaks if plugin defaults disable automation or expose a credential in displayable settings."""
    settings = load_settings({}, home=tmp_path)

    assert settings.api_url == DEFAULT_RAM0_API_URL
    assert settings.api_key is None
    assert settings.network_enabled is False
    assert settings.retrieval_enabled is True
    assert settings.capture_enabled is True
    assert settings.display() == {
        "api_url": DEFAULT_RAM0_API_URL,
        "network_enabled": False,
        "retrieval_enabled": True,
        "capture_enabled": True,
    }


def test_api_key_enables_network_and_boolean_overrides_are_parsed():
    """Breaks if network use no longer requires the key or local opt-outs are ignored."""
    settings = load_settings(
        {
            "RAM0_API_URL": "http://ram0.example.test/",
            "RAM0_API_KEY": "  ram0-test-key\t",
            "RAM0_MEMORY_RETRIEVAL": "false",
            "RAM0_MEMORY_CAPTURE": "0",
        }
    )

    assert settings.api_url == "http://ram0.example.test"
    assert settings.api_key == "ram0-test-key"
    assert settings.network_enabled is True
    assert settings.retrieval_enabled is False
    assert settings.capture_enabled is False
    assert "ram0-test-key" not in repr(settings.display())
    assert "ram0-test-key" not in repr(settings)


def test_owner_fingerprint_changes_with_endpoint_and_key_without_exposing_either():
    """Breaks if onboarding markers collide across endpoints or persist a credential-derived value."""
    first = load_settings({"RAM0_API_URL": "https://one.example", "RAM0_API_KEY": "same-key"})
    second = load_settings({"RAM0_API_URL": "https://two.example", "RAM0_API_KEY": "same-key"})
    third = load_settings({"RAM0_API_URL": "https://one.example", "RAM0_API_KEY": "other-key"})

    assert len({first.owner_fingerprint, second.owner_fingerprint, third.owner_fingerprint}) == 3
    assert all(
        len(value) == 64 for value in (first.owner_fingerprint, second.owner_fingerprint, third.owner_fingerprint)
    )
    assert "same-key" not in first.owner_fingerprint


def test_blank_api_key_keeps_network_disabled():
    """Breaks if a whitespace-only key is treated as a usable credential."""
    settings = load_settings({"RAM0_API_KEY": " \t "})

    assert settings.api_key is None
    assert settings.network_enabled is False


def test_every_marketplace_is_ram0_owned_and_exposes_only_the_local_ram0_plugin():
    """Breaks if the Ram0 fork displays Mem0 marketplace identity or catalogs an unrelated plugin."""
    for marketplace in MARKETPLACES:
        document = json.loads(marketplace.read_text())
        assert document["name"] == "ram0-plugins"
        plugins = document["plugins"]
        assert [plugin["name"] for plugin in plugins] == ["ram0"]
        assert "Mem0 Plugins" not in marketplace.read_text()
        ram0 = plugins[0]
        source = ram0["source"]
        path = source["path"] if isinstance(source, dict) else source
        assert path == "./integrations/ram0-plugin"

    assert (ROOT / "integrations/mem0-plugin").is_dir()
