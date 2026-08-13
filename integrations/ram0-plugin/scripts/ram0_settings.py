"""Local, display-safe configuration for the Ram0 plugin."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from ram0_config import DEFAULT_RAM0_API_URL as DEFAULT_RAM0_API_URL
from ram0_config import load_config


@dataclass(frozen=True)
class Ram0Settings:
    """Environment-derived controls; account identity remains server-derived."""

    api_url: str
    api_key: str | None = field(repr=False)
    retrieval_enabled: bool
    capture_enabled: bool

    @property
    def network_enabled(self) -> bool:
        return bool(self.api_key)

    @property
    def owner_fingerprint(self) -> str:
        """Return one non-reversible endpoint-and-owner scope for local state."""
        return hashlib.sha256(f"{self.api_url}\0{self.api_key or ''}".encode()).hexdigest()

    def display(self) -> dict[str, str | bool]:
        """Return settings suitable for local status output, never including the key."""
        return {
            "api_url": self.api_url,
            "network_enabled": self.network_enabled,
            "retrieval_enabled": self.retrieval_enabled,
            "capture_enabled": self.capture_enabled,
        }


def load_settings(environment: Mapping[str, str] | None = None, *, home: Path | None = None) -> Ram0Settings:
    """Read only the four explicit local settings, with safe automation defaults."""
    source = os.environ if environment is None else environment
    config = load_config(source, home=home)
    return Ram0Settings(
        api_url=config.api_url,
        api_key=config.api_key,
        retrieval_enabled=_parse_bool(source.get("RAM0_MEMORY_RETRIEVAL"), default=True),
        capture_enabled=_parse_bool(source.get("RAM0_MEMORY_CAPTURE"), default=True),
    )


def _parse_bool(value: str | None, *, default: bool) -> bool:
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError("RAM0_MEMORY_RETRIEVAL and RAM0_MEMORY_CAPTURE must be boolean values.")
