"""Fail-closed ownership migration for installations created before accounts existed."""

import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Literal, NamedTuple

from fastapi import HTTPException
from sqlalchemy import select, text

from db import SessionLocal
from models import CategoryJob, Settings, User
from server_state import get_memory_instance


OWNERSHIP_VERSION_KEY = "memory_ownership_version"
OWNERSHIP_VERSION = "1"
_MIGRATION_BATCH_SIZE = 100
_MAINTENANCE_DETAIL = "Memory ownership migration is in maintenance. Please try again later."
_OWNERSHIP_LOCK_KEY = "memory_ownership_version_1"


class OwnershipMigrationResult(NamedTuple):
    state: Literal["ready", "waiting_for_admin", "blocked"]
    migrated_memories: int
    migrated_jobs: int


class _OwnershipMigrationError(RuntimeError):
    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


def _flatten_rows(listed: object) -> list[object]:
    if not isinstance(listed, Sequence) or isinstance(listed, (str, bytes)):
        return []
    rows: list[object] = []
    for group in listed:
        if isinstance(group, Sequence) and not isinstance(group, (str, bytes)):
            rows.extend(group)
        else:
            rows.append(group)
    return rows


def _provider_name(memory: object) -> str | None:
    config = getattr(memory, "config", None)
    if isinstance(config, Mapping):
        vector_store = config.get("vector_store")
    else:
        vector_store = getattr(config, "vector_store", None)
    if isinstance(vector_store, Mapping):
        provider = vector_store.get("provider")
    else:
        provider = getattr(vector_store, "provider", None)
    return provider if isinstance(provider, str) else None


def _chunks(values: list[object]):
    for start in range(0, len(values), _MIGRATION_BATCH_SIZE):
        yield values[start : start + _MIGRATION_BATCH_SIZE]


def _persist_version(session) -> None:
    row = session.get(Settings, OWNERSHIP_VERSION_KEY)
    if row is None:
        session.add(Settings(key=OWNERSHIP_VERSION_KEY, value=OWNERSHIP_VERSION))
    else:
        row.value = OWNERSHIP_VERSION


def _ownership_ready(session) -> bool:
    row = session.get(Settings, OWNERSHIP_VERSION_KEY)
    return row is not None and row.value == OWNERSHIP_VERSION


def _blocked(
    migrated_memories: int,
    migrated_jobs: int,
    *,
    reason_code: str,
    exception_class: str = "none",
) -> OwnershipMigrationResult:
    logging.error(
        "memory_ownership_migration state=blocked reason_code=%s exception_class=%s "
        "migrated_memories=%d migrated_jobs=%d",
        reason_code,
        exception_class,
        migrated_memories,
        migrated_jobs,
    )
    return OwnershipMigrationResult("blocked", migrated_memories, migrated_jobs)


def migrate_legacy_ownership(
    session_factory: Callable = SessionLocal,
    memory_factory: Callable[[], object] = get_memory_instance,
) -> OwnershipMigrationResult:
    """Claim unowned legacy data only when exactly one administrator exists.

    The marker is deliberately committed last. A process interruption leaves the
    installation unready, and retrying repeats only merge-style pgvector patches.
    """
    migrated_memories = 0
    migrated_jobs = 0
    session = session_factory()
    try:
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
            {"lock_key": _OWNERSHIP_LOCK_KEY},
        )
        if _ownership_ready(session):
            return OwnershipMigrationResult("ready", 0, 0)

        memory = memory_factory()
        rows = _flatten_rows(memory.vector_store.list(top_k=None))
        jobs = list(session.scalars(select(CategoryJob)).all())
        if not rows and not jobs:
            _persist_version(session)
            session.commit()
            logging.info("memory_ownership_migration state=ready migrated_memories=0 migrated_jobs=0")
            return OwnershipMigrationResult("ready", 0, 0)

        users = list(session.scalars(select(User)).all())
        if not users:
            logging.info("memory_ownership_migration state=waiting_for_admin migrated_memories=0 migrated_jobs=0")
            return OwnershipMigrationResult("waiting_for_admin", 0, 0)
        if len(users) != 1:
            return _blocked(0, 0, reason_code="multiple_accounts")
        if users[0].role != "admin":
            return _blocked(0, 0, reason_code="invalid_account_principal")
        if _provider_name(memory) != "pgvector":
            return _blocked(0, 0, reason_code="unsupported_provider")

        owner_id = users[0].id
        owner_value = str(owner_id)
        unowned_jobs = []
        for category_job in jobs:
            if category_job.owner_id is None:
                unowned_jobs.append(category_job)
            elif category_job.owner_id != owner_id:
                raise _OwnershipMigrationError("foreign_category_job")
        rows_to_claim = []
        for row in rows:
            payload = getattr(row, "payload", None)
            if not isinstance(payload, Mapping):
                raise _OwnershipMigrationError("invalid_memory_record")
            if payload.get("user_id") != owner_value:
                rows_to_claim.append(row)

        for batch in _chunks(rows_to_claim):
            for row in batch:
                payload = row.payload
                patched = memory.vector_store._patch_payload(
                    row.id,
                    {"user_id": owner_value},
                    expected={"user_id": payload.get("user_id")},
                )
                if patched is None:
                    raise _OwnershipMigrationError("concurrent_memory_patch")
                migrated_memories += 1

        for batch in _chunks(unowned_jobs):
            for category_job in batch:
                category_job.owner_id = owner_id
                migrated_jobs += 1
        session.flush()

        verified_rows = _flatten_rows(memory.vector_store.list(top_k=None))
        if len(verified_rows) != len(rows):
            raise _OwnershipMigrationError("verification_failed")
        if any(
            not isinstance(getattr(row, "payload", None), Mapping) or row.payload.get("user_id") != owner_value
            for row in verified_rows
        ):
            raise _OwnershipMigrationError("verification_failed")
        if any(job_owner != owner_id for job_owner in session.scalars(select(CategoryJob.owner_id)).all()):
            raise _OwnershipMigrationError("verification_failed")

        _persist_version(session)
        session.commit()
        logging.info(
            "memory_ownership_migration state=ready migrated_memories=%d migrated_jobs=%d",
            migrated_memories,
            migrated_jobs,
        )
        return OwnershipMigrationResult("ready", migrated_memories, migrated_jobs)
    except _OwnershipMigrationError as error:
        session.rollback()
        return _blocked(
            migrated_memories,
            migrated_jobs,
            reason_code=error.reason_code,
            exception_class=type(error).__name__,
        )
    except Exception as error:
        session.rollback()
        return _blocked(
            migrated_memories,
            migrated_jobs,
            reason_code="unexpected_exception",
            exception_class=type(error).__name__,
        )
    finally:
        try:
            session.rollback()
        finally:
            session.close()


def require_ownership_ready(session_factory: Callable = SessionLocal) -> None:
    """Block protected routes until legacy data has a verified owner."""
    session = session_factory()
    try:
        if _ownership_ready(session):
            return
    except Exception as error:
        logging.error(
            "memory_ownership_migration state=blocked reason_code=readiness_check_failed "
            "exception_class=%s migrated_memories=0 migrated_jobs=0",
            type(error).__name__,
        )
    finally:
        session.close()
    raise HTTPException(status_code=503, detail=_MAINTENANCE_DETAIL)
