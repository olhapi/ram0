"""Invitation token primitives shared by public and administrative routes."""

import hashlib
import secrets
from datetime import datetime, timezone


def generate_invitation_token() -> str:
    """Generate an invitation secret that is returned to an administrator only once."""
    return secrets.token_urlsafe(32)


def hash_invitation_token(token: str) -> str:
    """Return the fixed-size database representation of an invitation secret."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def invitation_is_expired(expires_at: datetime, now: datetime) -> bool:
    """Compare invitation timestamps from PostgreSQL and naive SQLite fixtures."""
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= now
