"""Cross-table PostgreSQL fences for normalized account-email reservations."""

from sqlalchemy import text
from sqlalchemy.orm import Session


def lock_normalized_email(db: Session, normalized_email: str) -> None:
    """Serialize invite, acceptance, and profile-email writes for one normalized email."""
    if db.bind is None or db.bind.dialect.name != "postgresql":
        return
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
        {"lock_key": f"ram0-account-email:{normalized_email}"},
    )
