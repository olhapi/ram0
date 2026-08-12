"""Validated dashboard origin used by browser-facing server responses."""

import os
import re
from ipaddress import ip_address
from urllib.parse import urlsplit


_DEFAULT_DASHBOARD_URL = "http://localhost:3000"


def _invalid_origin() -> ValueError:
    return ValueError("DASHBOARD_URL must be a single canonical http(s) origin.")


def _looks_like_browser_ipv4(hostname: str) -> bool:
    """Match WHATWG's numeric final-label signal after strict IPv4 parsing fails."""
    final_label = hostname.rstrip(".").rsplit(".", 1)[-1]
    return final_label.isascii() and (final_label.isdigit() or bool(re.fullmatch(r"0x[0-9a-f]+", final_label)))


def validate_dashboard_origin(configured: str) -> str:
    """Validate and canonicalize one unambiguous HTTP(S) origin."""
    if (
        not configured
        or "\\" in configured
        or any(ord(character) < 32 or ord(character) == 127 for character in configured)
    ):
        raise _invalid_origin()
    try:
        parsed = urlsplit(configured)
        port = parsed.port
    except ValueError as error:
        raise _invalid_origin() from error

    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or any(character.isspace() for character in parsed.hostname)
        or "%" in parsed.hostname
        or parsed.netloc.endswith(":")
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or port is not None
        and not 0 < port <= 65535
        or port == 80
        and parsed.scheme == "http"
        or port == 443
        and parsed.scheme == "https"
    ):
        raise _invalid_origin()

    hostname = parsed.hostname
    try:
        address = ip_address(hostname)
    except ValueError:
        if _looks_like_browser_ipv4(hostname):
            raise _invalid_origin()
        labels = hostname.split(".")
        if any(
            label.lower().startswith("xn--") or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in labels
        ):
            raise _invalid_origin()
        canonical_host = hostname
    else:
        canonical_host = f"[{address.compressed}]" if address.version == 6 else address.compressed

    canonical_authority = canonical_host if port is None else f"{canonical_host}:{port}"
    canonical = f"{parsed.scheme}://{canonical_authority}"
    if configured not in {canonical, f"{canonical}/"}:
        raise _invalid_origin()
    return canonical


def dashboard_origin() -> str:
    """Load and validate the dashboard origin after environment setup."""
    return validate_dashboard_origin(os.environ.get("DASHBOARD_URL", _DEFAULT_DASHBOARD_URL))
