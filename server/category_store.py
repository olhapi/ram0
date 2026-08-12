"""Durable persistence for self-hosted category catalogs and classification jobs."""

import json
import logging
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from typing import Callable, Iterable, Iterator, Mapping

from sqlalchemy import and_, delete, or_, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from pydantic import BaseModel, ConfigDict, SkipValidation, ValidationError, field_serializer

from category_models import (
    CATEGORY_GENERATION_KEY,
    CATEGORY_ORIGIN_KEY,
    CategoryDefinition,
    CategoryJobState,
    validate_catalog,
)
from category_job_errors import (
    parse_terminal_error,
    safe_error_message,
    sanitize_error_code,
    terminal_marker,
)
from models import CategoryJob, Settings


_LEGACY_CATALOG_KEY = "custom_categories"
_ACTIVE_STATES = (CategoryJobState.QUEUED, CategoryJobState.PROCESSING, CategoryJobState.RETRYING)
_PREPARING_STATE = "preparing"
_CLAIM_BATCH_SIZE = 16
_EXPECTED_UNSET = object()


class CategoryCatalogStoreError(RuntimeError):
    """The stored category catalog could not be safely interpreted."""


class EnqueueResult(BaseModel):
    """The durable job selected or created for a memory."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: uuid.UUID
    created: bool


class PreparedCategoryJob(BaseModel):
    """Internal durable preparation discovered during startup recovery."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    memory_id: str
    owner_id: uuid.UUID
    memory_hash: str | None


class ClaimedCategoryJob(BaseModel):
    """The immutable job data a worker may process after its lease commits."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: uuid.UUID
    memory_id: str
    owner_id: uuid.UUID
    memory_hash: str | None
    catalog: tuple[CategoryDefinition, ...]
    attempts: int
    terminalizing: bool = False
    terminal_error_code: str | None = None
    terminal_error_message: str | None = None

    @property
    def catalog_snapshot(self) -> tuple[CategoryDefinition, ...]:
        """Expose the immutable catalog under the durable-column name too."""
        return self.catalog


class MemorySnapshot(BaseModel):
    """The category-relevant fields from one vector-store memory payload."""

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    memory_id: str
    user_id: uuid.UUID
    text: str
    memory_hash: str | None
    categories: tuple[str, ...] | None
    category_status: str
    category_generation: str | None = None
    category_origin: str | None = None
    payload: SkipValidation[Mapping[str, object]]

    @field_serializer("payload")
    def serialize_payload(self, payload: Mapping[str, object]) -> dict[str, object]:
        """Serialize immutable mapping proxies without weakening the snapshot."""
        return {key: self._serializable(value) for key, value in payload.items()}

    @classmethod
    def _serializable(cls, value: object) -> object:
        if isinstance(value, Mapping):
            return {key: cls._serializable(item) for key, item in value.items()}
        if isinstance(value, (tuple, list, frozenset, set)):
            return [cls._serializable(item) for item in value]
        return value


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _catalog_json(catalog: Iterable[CategoryDefinition]) -> list[dict[str, str]]:
    return [definition.model_dump() for definition in catalog]


def _catalog_key(owner_id: str) -> str:
    return f"{_LEGACY_CATALOG_KEY}:{uuid.UUID(owner_id)}"


def _catalog_lock_key(owner_id: str) -> str:
    return f"category-catalog:{uuid.UUID(owner_id)}"


class CategoryCatalogStore:
    """Persist each owner's optional category catalog in the existing settings table."""

    def __init__(self, session_factory: Callable[[], Session]):
        self._session_factory = session_factory

    def get_saved(self, owner_id: str, *, session: Session | None = None) -> tuple[CategoryDefinition, ...]:
        owned = session is None
        session = session or self._session_factory()
        try:
            session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
                {"lock_key": _catalog_lock_key(owner_id)},
            )
            _, saved = self._load_or_seed(session, owner_id)
            if owned:
                session.commit()
            return saved
        except Exception:
            if owned:
                session.rollback()
            raise
        finally:
            if owned:
                session.close()

    def replace(
        self,
        owner_id: str,
        definitions: tuple[CategoryDefinition, ...] | list[CategoryDefinition],
    ) -> tuple[CategoryDefinition, ...]:
        catalog = validate_catalog(definitions)
        return self._mutate(owner_id, lambda _saved: catalog)

    def create(self, owner_id: str, definition: CategoryDefinition) -> tuple[CategoryDefinition, ...]:
        """Append against the catalog loaded under the mutation transaction."""
        return self._mutate(owner_id, lambda saved: validate_catalog((*saved, definition)))

    def update(
        self,
        owner_id: str,
        name: str,
        *,
        new_name: str | None = None,
        description: str | None = None,
    ) -> tuple[CategoryDefinition, ...]:
        """Apply only supplied fields to the latest locked definition."""

        def mutate(saved: tuple[CategoryDefinition, ...]) -> tuple[CategoryDefinition, ...]:
            existing = next((item for item in saved if item.name == name), None)
            if existing is None:
                raise KeyError(name)
            replacement = CategoryDefinition(
                name=new_name if new_name is not None else existing.name,
                description=description if description is not None else existing.description,
            )
            return validate_catalog(tuple(replacement if item.name == name else item for item in saved))

        return self._mutate(owner_id, mutate)

    def delete(self, owner_id: str, name: str) -> tuple[CategoryDefinition, ...]:
        """Delete from the latest catalog while holding the mutation transaction."""

        def mutate(saved: tuple[CategoryDefinition, ...]) -> tuple[CategoryDefinition, ...]:
            updated = tuple(item for item in saved if item.name != name)
            if len(updated) == len(saved):
                raise KeyError(name)
            return updated

        return self._mutate(owner_id, mutate)

    def _mutate(
        self,
        owner_id: str,
        mutation: Callable[[tuple[CategoryDefinition, ...]], tuple[CategoryDefinition, ...]],
    ) -> tuple[CategoryDefinition, ...]:
        """Serialize project catalog mutation, including the initially absent row."""
        session = self._session_factory()
        try:
            session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
                {"lock_key": _catalog_lock_key(owner_id)},
            )
            row, saved = self._load_or_seed(session, owner_id)
            catalog = validate_catalog(mutation(saved))
            serialized = json.dumps(_catalog_json(catalog))
            row.value = serialized
            session.commit()
            return catalog
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _load_or_seed(self, session: Session, owner_id: str) -> tuple[Settings, tuple[CategoryDefinition, ...]]:
        """Load one locked owner row, copying the validated legacy template on first access."""
        key = _catalog_key(owner_id)
        row = session.get(Settings, key, with_for_update=True)
        if row is not None:
            return row, self._decode(row)

        legacy = session.get(Settings, _LEGACY_CATALOG_KEY)
        saved = self._decode(legacy)
        row = Settings(key=key, value=legacy.value if legacy is not None else json.dumps(_catalog_json(saved)))
        session.add(row)
        return row, saved

    @staticmethod
    def _decode(row: Settings | None) -> tuple[CategoryDefinition, ...]:
        if row is None:
            return ()
        try:
            value = json.loads(row.value)
            if not isinstance(value, list):
                raise ValueError("Catalog JSON must be a list.")
            return validate_catalog(tuple(CategoryDefinition.model_validate(item) for item in value))
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            logging.error("Failed to load stored category catalog", exc_info=True)
            raise CategoryCatalogStoreError("The stored category catalog is invalid.") from error


class CategoryJobStore:
    """Create, lease, retry, and cancel durable category-classification jobs."""

    def __init__(self, session_factory: Callable[[], Session]):
        self._session_factory = session_factory
        self._owner_fence_session: ContextVar[Session | None] = ContextVar(
            f"category_owner_fence_session_{id(self)}", default=None
        )

    @contextmanager
    def owner_fence(self, owner_id: uuid.UUID):
        """Serialize owner-wide mutations across processes; acquire before any memory fence."""
        with self._advisory_fence(f"category-owner:{owner_id}", blocking=True) as session:
            if session is None:  # pragma: no cover - blocking acquisition always succeeds or raises
                raise RuntimeError("Failed to acquire category owner fence.")
            token = self._owner_fence_session.set(session)
            try:
                yield session
            finally:
                self._owner_fence_session.reset(token)

    @contextmanager
    def memory_fence(self, memory_id: str):
        """Yield one pinned session protected by a cross-transaction advisory lock."""
        with self._advisory_fence(
            f"category-memory:{memory_id}",
            blocking=True,
            session=self._owner_fence_session.get(),
        ) as session:
            if session is None:  # pragma: no cover - blocking acquisition always succeeds or raises
                raise RuntimeError("Failed to acquire category memory fence.")
            yield session

    @contextmanager
    def try_memory_fence(self, memory_id: str):
        """Yield a pinned session only when this memory's advisory lock is immediately available."""
        with self._advisory_fence(
            f"category-memory:{memory_id}",
            blocking=False,
            session=self._owner_fence_session.get(),
        ) as session:
            yield session

    @contextmanager
    def _advisory_fence(
        self,
        lock_key: str,
        *,
        blocking: bool,
        session: Session | None = None,
    ):
        owned = session is None
        factory_session = session or self._session_factory()
        connection = None
        bind = factory_session.get_bind()
        if owned and isinstance(bind, Engine):
            factory_session.close()
            connection = bind.connect()
            session = Session(
                bind=connection,
                autoflush=factory_session.autoflush,
                expire_on_commit=factory_session.expire_on_commit,
            )
        else:
            session = factory_session
        acquired = False
        try:
            lock_function = "pg_advisory_lock" if blocking else "pg_try_advisory_lock"
            result = session.execute(
                text(f"SELECT {lock_function}(hashtextextended(:lock_key, 0))"),
                {"lock_key": lock_key},
            )
            acquired = blocking or bool(result.scalar_one())
            session.commit()
            yield session if acquired else None
        except Exception:
            session.rollback()
            raise
        finally:
            try:
                session.rollback()
                if acquired:
                    session.execute(
                        text("SELECT pg_advisory_unlock(hashtextextended(:lock_key, 0))"),
                        {"lock_key": lock_key},
                    )
                    session.commit()
            except Exception:
                if connection is not None:
                    connection.invalidate()
                raise
            finally:
                if owned:
                    session.close()
                    if connection is not None:
                        connection.close()

    def prepare(
        self,
        memory_id: str,
        memory_hash: str | None,
        catalog: tuple[CategoryDefinition, ...] | list[CategoryDefinition],
        *,
        job_id: uuid.UUID,
        owner_id: uuid.UUID,
        session: Session | None = None,
    ) -> EnqueueResult:
        """Durably store an immutable catalog without making the job active or claimable."""
        snapshot = _catalog_json(validate_catalog(catalog))
        owned = session is None
        session = session or self._session_factory()
        try:
            existing = session.get(CategoryJob, job_id)
            if existing is not None:
                same_preparation = (
                    existing.memory_id == memory_id
                    and existing.owner_id == owner_id
                    and existing.memory_hash == memory_hash
                    and existing.catalog_snapshot == snapshot
                )
                if same_preparation and existing.state == _PREPARING_STATE:
                    return EnqueueResult(job_id=job_id, created=False)
                if (
                    same_preparation
                    and existing.state == CategoryJobState.CANCELLED
                    and existing.error_code == "replaced"
                ):
                    existing.state = _PREPARING_STATE
                    existing.error_code = None
                    existing.error_message = None
                    existing.worker_id = None
                    existing.lease_expires_at = None
                    existing.started_at = None
                    existing.completed_at = None
                    existing.next_attempt_at = None
                    existing.attempts = 0
                    session.commit()
                    return EnqueueResult(job_id=job_id, created=True)
                if not same_preparation:
                    raise RuntimeError("Category preparation token conflicts with an existing job.")
                raise RuntimeError("Category preparation token is no longer retryable.")
            job = CategoryJob(
                id=job_id,
                memory_id=memory_id,
                owner_id=owner_id,
                memory_hash=memory_hash,
                catalog_snapshot=snapshot,
                state=_PREPARING_STATE,
                next_attempt_at=None,
            )
            session.add(job)
            session.commit()
            return EnqueueResult(job_id=job_id, created=True)
        except Exception:
            session.rollback()
            raise
        finally:
            if owned:
                session.close()

    def install_prepared(
        self, job_id: uuid.UUID, owner_id: uuid.UUID, *, session: Session | None = None
    ) -> bool:
        """Atomically replace active work with one already-durable prepared catalog."""
        owned = session is None
        session = session or self._session_factory()
        try:
            prepared = self._load_for_update(session, job_id)
            if prepared is not None and prepared.owner_id == owner_id and prepared.state in _ACTIVE_STATES:
                return True
            if prepared is None or prepared.owner_id != owner_id or prepared.state != _PREPARING_STATE:
                return False
            active = self._load_active(session, prepared.memory_id)
            if active is not None:
                if active.owner_id != owner_id:
                    return False
                self._cancel_row(active, "replaced", now=_utcnow())
                session.flush()
            prepared.state = CategoryJobState.QUEUED
            prepared.next_attempt_at = _utcnow()
            session.commit()
            return True
        except Exception:
            session.rollback()
            raise
        finally:
            if owned:
                session.close()

    def cancel_prepared(
        self, job_id: uuid.UUID, owner_id: uuid.UUID, *, session: Session | None = None
    ) -> bool:
        """Cancel only a reservation that never became active."""
        owned = session is None
        session = session or self._session_factory()
        try:
            job = self._load_for_update(session, job_id)
            if job is None or job.owner_id != owner_id or job.state != _PREPARING_STATE:
                return False
            self._cancel_row(job, "replaced", now=_utcnow())
            session.commit()
            return True
        except Exception:
            session.rollback()
            raise
        finally:
            if owned:
                session.close()

    def prepared_matches(
        self,
        memory_id: str,
        memory_hash: str | None,
        generation: str,
        *,
        owner_id: uuid.UUID,
        session: Session | None = None,
    ) -> bool:
        """Return whether a marker names its still-prepared durable catalog row."""
        try:
            job_id = uuid.UUID(generation)
        except (TypeError, ValueError):
            return False
        owned = session is None
        session = session or self._session_factory()
        try:
            job = session.get(CategoryJob, job_id)
            return bool(
                job
                and job.state == _PREPARING_STATE
                and job.memory_id == memory_id
                and job.owner_id == owner_id
                and job.memory_hash == memory_hash
            )
        finally:
            if owned:
                session.close()

    def list_prepared(self) -> tuple[PreparedCategoryJob, ...]:
        """Return internal preparations for startup repair without exposing them publicly."""
        session = self._session_factory()
        try:
            rows = session.execute(
                select(CategoryJob)
                .where(CategoryJob.state == _PREPARING_STATE)
                .order_by(CategoryJob.created_at, CategoryJob.id)
            ).scalars().all()
            valid = []
            invalid = []
            for row in rows:
                if row.owner_id is None:
                    self._cancel_row(row, "owner_invalid", now=_utcnow())
                    invalid.append(row)
                    logging.warning(
                        "category_job_owner_invalid job_id=%s memory_id=%s error_code=owner_invalid",
                        row.id,
                        row.memory_id,
                    )
                    continue
                valid.append(
                    PreparedCategoryJob(
                        id=row.id,
                        memory_id=row.memory_id,
                        owner_id=row.owner_id,
                        memory_hash=row.memory_hash,
                    )
                )
            if invalid:
                session.commit()
            return tuple(valid)
        finally:
            session.close()

    def preparation_is_latest(
        self,
        job_id: uuid.UUID,
        memory_id: str,
        owner_id: uuid.UUID,
        *,
        session: Session | None = None,
    ) -> bool:
        """Fence recovery to the newest durable work row for one memory."""
        owned = session is None
        session = session or self._session_factory()
        try:
            preparation = self._load_for_update(session, job_id)
            if (
                preparation is None
                or preparation.owner_id != owner_id
                or preparation.state != _PREPARING_STATE
            ):
                return False
            newest = session.execute(
                select(CategoryJob)
                .where(CategoryJob.memory_id == memory_id, CategoryJob.owner_id == owner_id)
                .order_by(CategoryJob.created_at.desc(), CategoryJob.id.desc())
                .limit(1)
                .with_for_update()
            ).scalar_one_or_none()
            return bool(newest and newest.id == job_id)
        finally:
            if owned:
                session.close()

    def active_matches(
        self,
        memory_id: str,
        memory_hash: str | None,
        generation: str,
        *,
        owner_id: uuid.UUID,
        session: Session | None = None,
    ) -> bool:
        """Check whether the pending payload token names the current active hash snapshot."""
        owned = session is None
        session = session or self._session_factory()
        try:
            active = session.execute(
                select(CategoryJob)
                .where(
                    CategoryJob.memory_id == memory_id,
                    CategoryJob.owner_id == owner_id,
                    CategoryJob.state.in_(_ACTIVE_STATES),
                )
                .order_by(CategoryJob.created_at)
                .limit(1)
            ).scalar_one_or_none()
            return bool(
                active
                and active.owner_id == owner_id
                and str(active.id) == generation
                and active.memory_hash == memory_hash
            )
        finally:
            if owned:
                session.close()

    def get(self, job_id: uuid.UUID) -> CategoryJob | None:
        session = self._session_factory()
        try:
            return session.get(CategoryJob, job_id)
        finally:
            session.close()

    def claim(self, worker_id: str, now: datetime, lease_seconds: int) -> ClaimedCategoryJob | None:
        ready = and_(
            CategoryJob.state.in_((CategoryJobState.QUEUED, CategoryJobState.RETRYING)),
            or_(CategoryJob.next_attempt_at.is_(None), CategoryJob.next_attempt_at <= now),
        )
        expired = and_(
            CategoryJob.state == CategoryJobState.PROCESSING,
            CategoryJob.lease_expires_at <= now,
        )
        cursor: tuple[datetime, uuid.UUID] | None = None
        while True:
            candidate_session = self._session_factory()
            try:
                statement = select(CategoryJob).where(or_(ready, expired))
                if cursor is not None:
                    created_at, job_id = cursor
                    statement = statement.where(
                        or_(
                            CategoryJob.created_at > created_at,
                            and_(CategoryJob.created_at == created_at, CategoryJob.id > job_id),
                        )
                    )
                candidates = candidate_session.execute(
                    statement.order_by(CategoryJob.created_at, CategoryJob.id).limit(_CLAIM_BATCH_SIZE)
                ).scalars().all()
            finally:
                candidate_session.close()
            if not candidates:
                return None

            for candidate in candidates:
                with self.try_memory_fence(candidate.memory_id) as session:
                    if session is None:
                        continue
                    try:
                        job = self._load_for_update(session, candidate.id)
                        if not self._claimable(job, now):
                            continue
                        if job.owner_id is None:
                            self._cancel_row(job, "owner_invalid", now=now)
                            session.commit()
                            logging.warning(
                                "category_job_owner_invalid job_id=%s memory_id=%s error_code=owner_invalid",
                                job.id,
                                job.memory_id,
                            )
                            continue
                        terminal = parse_terminal_error(job.error_code)
                        if terminal is not None and terminal[2]:
                            job.error_code = terminal_marker("category_error", 0)
                            job.error_message = safe_error_message("category_error")
                            job.state = CategoryJobState.RETRYING
                            job.next_attempt_at = now + timedelta(seconds=2)
                            self._clear_lease(job)
                            session.commit()
                            logging.warning(
                                "category_job_marker_invalid job_id=%s memory_id=%s error_code=category_error",
                                job.id,
                                job.memory_id,
                            )
                            continue
                        terminal_error_code = terminal[0] if terminal is not None else None
                        if terminal is not None:
                            catalog = ()
                        else:
                            try:
                                catalog = validate_catalog(
                                    tuple(
                                        CategoryDefinition.model_validate(item)
                                        for item in job.catalog_snapshot
                                    )
                                )
                            except (TypeError, ValueError):
                                job.error_code = terminal_marker("category_error", 0)
                                job.error_message = safe_error_message("category_error")
                                job.state = CategoryJobState.RETRYING
                                job.next_attempt_at = now + timedelta(seconds=2)
                                self._clear_lease(job)
                                session.commit()
                                logging.warning(
                                    "category_job_catalog_invalid job_id=%s memory_id=%s error_code=category_error",
                                    job.id,
                                    job.memory_id,
                                )
                                continue
                        job.state = CategoryJobState.PROCESSING
                        job.worker_id = worker_id
                        job.lease_expires_at = now + timedelta(seconds=lease_seconds)
                        job.started_at = now
                        if terminal_error_code is None:
                            job.attempts += 1
                        session.commit()
                        return ClaimedCategoryJob(
                            id=job.id,
                            memory_id=job.memory_id,
                            owner_id=job.owner_id,
                            memory_hash=job.memory_hash,
                            catalog=catalog,
                            attempts=job.attempts,
                            terminalizing=terminal_error_code is not None,
                            terminal_error_code=terminal_error_code,
                            terminal_error_message=(
                                safe_error_message(terminal_error_code)
                                if terminal_error_code is not None
                                else None
                            ),
                        )
                    except Exception:
                        session.rollback()
                        raise
            if len(candidates) < _CLAIM_BATCH_SIZE:
                return None
            last = candidates[-1]
            cursor = (last.created_at, last.id)

    @staticmethod
    def _claimable(job: CategoryJob | None, now: datetime) -> bool:
        if job is None:
            return False
        if job.state in (CategoryJobState.QUEUED, CategoryJobState.RETRYING):
            return job.next_attempt_at is None or job.next_attempt_at <= now
        return bool(
            job.state == CategoryJobState.PROCESSING
            and job.lease_expires_at is not None
            and job.lease_expires_at <= now
        )

    def complete(
        self,
        job_id: uuid.UUID,
        worker_id: str,
        *,
        owner_id: uuid.UUID,
        now: datetime | None = None,
        session: Session | None = None,
        lease_fenced: bool = False,
    ) -> bool:
        owned = session is None
        session = session or self._session_factory()
        try:
            completed_at = now or _utcnow()
            job = self._load_for_update(session, job_id)
            if not (
                self._owned_by(job, worker_id, owner_id)
                if lease_fenced
                else self._claimed_by(job, worker_id, owner_id, completed_at)
            ):
                return False
            job.state = CategoryJobState.COMPLETED
            job.completed_at = completed_at
            self._clear_lease(job)
            session.commit()
            return True
        except Exception:
            session.rollback()
            raise
        finally:
            if owned:
                session.close()

    def renew(
        self,
        job_id: uuid.UUID,
        worker_id: str,
        *,
        owner_id: uuid.UUID,
        now: datetime,
        lease_seconds: int,
        session: Session | None = None,
    ) -> bool:
        """Extend only the current worker's still-valid lease before a payload write."""
        owned = session is None
        session = session or self._session_factory()
        try:
            job = self._load_for_update(session, job_id)
            if not self._claimed_by(job, worker_id, owner_id, now):
                return False
            job.lease_expires_at = now + timedelta(seconds=lease_seconds)
            session.commit()
            return True
        except Exception:
            session.rollback()
            raise
        finally:
            if owned:
                session.close()

    def reschedule_or_fail(
        self,
        job_id: uuid.UUID,
        worker_id: str,
        *,
        owner_id: uuid.UUID,
        now: datetime,
        error_code: str,
        error_message: str,
        max_attempts: int = 3,
        session: Session | None = None,
    ) -> CategoryJobState | None:
        owned = session is None
        session = session or self._session_factory()
        try:
            job = self._load_for_update(session, job_id)
            if not self._claimed_by(job, worker_id, owner_id, now):
                return None
            safe_code = sanitize_error_code(error_code)
            job.error_code = safe_code
            job.error_message = safe_error_message(safe_code)
            if job.attempts >= max_attempts:
                # Keep the live claim recoverable until the hash-guarded payload
                # has actually moved out of ``pending``.
                job.error_code = terminal_marker(safe_code, 0)
                session.commit()
                return CategoryJobState.PROCESSING
            else:
                self._clear_lease(job)
                job.state = CategoryJobState.RETRYING
                job.next_attempt_at = now + timedelta(seconds=min(2**job.attempts, 60))
            session.commit()
            return CategoryJobState(job.state)
        except Exception:
            session.rollback()
            raise
        finally:
            if owned:
                session.close()

    def reschedule_terminalization(
        self,
        job_id: uuid.UUID,
        worker_id: str,
        *,
        owner_id: uuid.UUID,
        now: datetime,
        max_backoff_seconds: int = 60,
        session: Session | None = None,
        lease_fenced: bool = False,
    ) -> bool:
        """Retry only a durable failed-payload write without returning to classification."""
        owned = session is None
        session = session or self._session_factory()
        try:
            job = self._load_for_update(session, job_id)
            terminal = parse_terminal_error(job.error_code)
            owned_attempt = (
                self._owned_by(job, worker_id, owner_id)
                if lease_fenced
                else self._claimed_by(job, worker_id, owner_id, now)
            )
            if not owned_attempt or terminal is None:
                return False
            error_code, retries, malformed = terminal
            if malformed:
                return False
            retries += 1
            delay = min(2 ** min(retries, 10), max_backoff_seconds)
            job.error_code = terminal_marker(error_code, retries)
            job.state = CategoryJobState.RETRYING
            job.next_attempt_at = now + timedelta(seconds=delay)
            self._clear_lease(job)
            session.commit()
            return True
        except Exception:
            session.rollback()
            raise
        finally:
            if owned:
                session.close()

    def fail(
        self,
        job_id: uuid.UUID,
        worker_id: str,
        *,
        owner_id: uuid.UUID,
        now: datetime,
        error_code: str,
        error_message: str,
        session: Session | None = None,
        lease_fenced: bool = False,
    ) -> bool:
        """Terminalize a live claim only after its failed payload write succeeds."""
        owned = session is None
        session = session or self._session_factory()
        try:
            job = self._load_for_update(session, job_id)
            if not (
                self._owned_by(job, worker_id, owner_id)
                if lease_fenced
                else self._claimed_by(job, worker_id, owner_id, now)
            ):
                return False
            job.error_code = sanitize_error_code(error_code)
            job.error_message = safe_error_message(job.error_code)
            job.state = CategoryJobState.FAILED
            job.completed_at = now
            job.next_attempt_at = None
            self._clear_lease(job)
            session.commit()
            return True
        except Exception:
            session.rollback()
            raise
        finally:
            if owned:
                session.close()

    def cancel(
        self,
        job_id: uuid.UUID,
        worker_id: str,
        reason_code: str,
        *,
        owner_id: uuid.UUID,
        now: datetime | None = None,
        session: Session | None = None,
        lease_fenced: bool = False,
    ) -> bool:
        owned = session is None
        session = session or self._session_factory()
        try:
            cancelled_at = now or _utcnow()
            job = self._load_for_update(session, job_id)
            if not (
                self._owned_by(job, worker_id, owner_id)
                if lease_fenced
                else self._claimed_by(job, worker_id, owner_id, cancelled_at)
            ):
                return False
            self._cancel_row(job, reason_code, now=cancelled_at)
            session.commit()
            return True
        except Exception:
            session.rollback()
            raise
        finally:
            if owned:
                session.close()

    def cancel_active(self, memory_id: str, owner_id: uuid.UUID) -> int:
        return self._cancel_matching(
            and_(CategoryJob.memory_id == memory_id, CategoryJob.owner_id == owner_id),
            "memory_deleted",
        )

    def cancel_all_active(self, owner_id: uuid.UUID) -> int:
        return self._cancel_matching(
            and_(CategoryJob.owner_id == owner_id, CategoryJob.state.in_(_ACTIVE_STATES)),
            "reset",
        )

    def purge_owner(self, owner_id: uuid.UUID) -> int:
        """Delete every durable category-job row for exactly one owner."""
        session = self._owner_fence_session.get()
        owned = session is None
        session = session or self._session_factory()
        try:
            result = session.execute(delete(CategoryJob).where(CategoryJob.owner_id == owner_id))
            session.commit()
            return result.rowcount or 0
        except Exception:
            session.rollback()
            raise
        finally:
            if owned:
                session.close()

    def list_jobs(
        self,
        *,
        owner_id: uuid.UUID,
        states: tuple[CategoryJobState, ...] | None = None,
        limit: int = 100,
    ) -> list[CategoryJob]:
        session = self._session_factory()
        try:
            statement = select(CategoryJob).order_by(CategoryJob.created_at.desc()).limit(limit)
            statement = statement.where(CategoryJob.owner_id == owner_id, CategoryJob.state != _PREPARING_STATE)
            if states:
                statement = statement.where(CategoryJob.state.in_(states))
            return list(session.execute(statement).scalars().all())
        finally:
            session.close()

    def _cancel_matching(self, condition: object, reason_code: str) -> int:
        session = self._session_factory()
        try:
            rows = session.execute(
                select(CategoryJob)
                .where(condition, CategoryJob.state.in_(_ACTIVE_STATES))
                .with_for_update()
            ).scalars().all()
            now = _utcnow()
            for job in rows:
                self._cancel_row(job, reason_code, now=now)
            if rows:
                session.commit()
            return len(rows)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @staticmethod
    def _load_active(session: Session, memory_id: str) -> CategoryJob | None:
        return session.execute(
            select(CategoryJob)
            .where(CategoryJob.memory_id == memory_id, CategoryJob.state.in_(_ACTIVE_STATES))
            .order_by(CategoryJob.created_at)
            .limit(1)
            .with_for_update()
        ).scalar_one_or_none()

    @staticmethod
    def _load_for_update(session: Session, job_id: uuid.UUID) -> CategoryJob | None:
        return session.execute(
            select(CategoryJob).where(CategoryJob.id == job_id).with_for_update()
        ).scalar_one_or_none()

    @staticmethod
    def _claimed_by(
        job: CategoryJob | None, worker_id: str, owner_id: uuid.UUID, now: datetime
    ) -> bool:
        return bool(
            CategoryJobStore._owned_by(job, worker_id, owner_id)
            and job.lease_expires_at is not None
            and job.lease_expires_at > now
        )

    @staticmethod
    def _owned_by(job: CategoryJob | None, worker_id: str, owner_id: uuid.UUID) -> bool:
        return bool(
            job
            and job.owner_id == owner_id
            and job.state == CategoryJobState.PROCESSING
            and job.worker_id == worker_id
        )

    @staticmethod
    def _clear_lease(job: CategoryJob) -> None:
        job.worker_id = None
        job.lease_expires_at = None

    @classmethod
    def _cancel_row(cls, job: CategoryJob, reason_code: str, *, now: datetime) -> None:
        job.state = CategoryJobState.CANCELLED
        job.error_code = sanitize_error_code(reason_code)
        job.error_message = None
        job.completed_at = now
        job.next_attempt_at = None
        cls._clear_lease(job)


class MemoryCategoryStore:
    """Persist category fields directly in existing vector-store payloads."""

    def __init__(self, memory_factory: Callable[[], object]):
        self._memory_factory = memory_factory

    def get(self, memory_id: str) -> MemorySnapshot | None:
        row = self._memory_factory().vector_store.get(memory_id)
        return self._snapshot(row, memory_id)

    def mark_pending(
        self,
        memory_id: str,
        generation: str,
        *,
        owner_id: uuid.UUID,
        expected_hash: object = _EXPECTED_UNSET,
        expected_text: str | None = None,
        expected_generation: object = _EXPECTED_UNSET,
        expected_origin: object = _EXPECTED_UNSET,
    ) -> MemorySnapshot | None:
        memory = self._memory_factory()
        expected = {"user_id": str(owner_id)}
        if expected_hash is not _EXPECTED_UNSET:
            expected["hash"] = expected_hash
        if expected_text is not None:
            expected["data"] = expected_text
        if expected_generation is not _EXPECTED_UNSET:
            expected[CATEGORY_GENERATION_KEY] = expected_generation
        fields = {
            "categories": None,
            "category_status": "pending",
            CATEGORY_GENERATION_KEY: generation,
        }
        if expected_origin is not _EXPECTED_UNSET:
            expected[CATEGORY_ORIGIN_KEY] = expected_origin
            fields[CATEGORY_ORIGIN_KEY] = None
        row = memory.vector_store._patch_payload(
            memory_id,
            fields,
            **({"expected": expected} if expected else {}),
        )
        return self._snapshot(row, memory_id)

    def write_result(
        self,
        memory_id: str,
        memory_hash: str | None,
        generation: str,
        categories: list[str],
        category_status: str,
        *,
        owner_id: uuid.UUID,
    ) -> bool:
        memory = self._memory_factory()
        row = memory.vector_store._patch_payload(
            memory_id,
            {
                "categories": list(categories),
                "category_status": category_status,
                CATEGORY_GENERATION_KEY: None,
            },
            expected={
                "user_id": str(owner_id),
                "hash": memory_hash,
                CATEGORY_GENERATION_KEY: generation,
                CATEGORY_ORIGIN_KEY: None,
            },
        )
        return row is not None

    def fail_origin(self, snapshot: MemorySnapshot) -> bool:
        """Clear an unrecoverable request marker without touching unrelated payload fields."""
        row = self._memory_factory().vector_store._patch_payload(
            snapshot.memory_id,
            {
                "categories": [],
                "category_status": "failed",
                CATEGORY_GENERATION_KEY: None,
                CATEGORY_ORIGIN_KEY: None,
            },
            expected={
                "user_id": str(snapshot.user_id),
                "hash": snapshot.memory_hash,
                CATEGORY_GENERATION_KEY: snapshot.category_generation,
                CATEGORY_ORIGIN_KEY: snapshot.category_origin,
            },
        )
        return row is not None

    def clear_origin(self, snapshot: MemorySnapshot) -> bool:
        """Remove only a stale request marker while preserving foreign generation ownership."""
        row = self._memory_factory().vector_store._patch_payload(
            snapshot.memory_id,
            {CATEGORY_ORIGIN_KEY: None},
            expected={
                "user_id": str(snapshot.user_id),
                "hash": snapshot.memory_hash,
                CATEGORY_GENERATION_KEY: snapshot.category_generation,
                CATEGORY_ORIGIN_KEY: snapshot.category_origin,
            },
        )
        return row is not None

    def restore(self, snapshot: MemorySnapshot, *, expected_generation: str) -> bool:
        """Restore only category fields if a failed install still owns the pending marker."""
        row = self._memory_factory().vector_store._patch_payload(
            snapshot.memory_id,
            {
                "categories": list(snapshot.categories) if snapshot.categories is not None else None,
                "category_status": snapshot.category_status,
                CATEGORY_GENERATION_KEY: snapshot.category_generation,
            },
            expected={
                "user_id": str(snapshot.user_id),
                "hash": snapshot.memory_hash,
                CATEGORY_GENERATION_KEY: expected_generation,
                CATEGORY_ORIGIN_KEY: None,
            },
        )
        return row is not None

    def iter_snapshots(self, owner_id: str) -> Iterator[MemorySnapshot]:
        rows = self._memory_factory().vector_store.list(filters={"user_id": owner_id}, top_k=None)
        yield from self._snapshots_from_rows(rows)

    def iter_all_snapshots(self) -> Iterator[MemorySnapshot]:
        """Yield every snapshot only for background recovery work."""
        rows = self._memory_factory().vector_store.list(top_k=None)
        yield from self._snapshots_from_rows(rows)

    def _snapshots_from_rows(self, rows: object) -> Iterator[MemorySnapshot]:
        for group in rows:
            if not isinstance(group, list):
                continue
            for row in group:
                snapshot = self._snapshot(row)
                if snapshot is not None:
                    yield snapshot

    def category_counts(self, owner_id: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for snapshot in self.iter_snapshots(owner_id):
            for category in snapshot.categories or []:
                counts[category] = counts.get(category, 0) + 1
        return counts

    @staticmethod
    def _snapshot(row: object, memory_id: str | None = None) -> MemorySnapshot | None:
        if row is None:
            return None
        payload = MemoryCategoryStore._payload(row)
        row_id = memory_id or getattr(row, "id", None)
        if not isinstance(row_id, str) or payload is None:
            return None
        try:
            return MemoryCategoryStore._snapshot_from_payload(row_id, payload)
        except ValidationError:
            logging.warning(
                "category_memory_owner_invalid memory_id=%s error_code=owner_invalid",
                row_id,
            )
            return None

    @staticmethod
    def _payload(row: object) -> dict | None:
        payload = getattr(row, "payload", None)
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _snapshot_from_payload(memory_id: str, payload: dict) -> MemorySnapshot:
        frozen_payload = MemoryCategoryStore._freeze_payload(payload)
        categories = frozen_payload.get("categories")
        text = frozen_payload.get("data", "")
        memory_hash = frozen_payload.get("hash")
        category_status = frozen_payload.get("category_status", "unclassified")
        category_generation = frozen_payload.get(CATEGORY_GENERATION_KEY)
        category_origin = frozen_payload.get(CATEGORY_ORIGIN_KEY)
        return MemorySnapshot(
            memory_id=memory_id,
            user_id=frozen_payload.get("user_id"),
            text=text if isinstance(text, str) else "",
            memory_hash=memory_hash if isinstance(memory_hash, str) else None,
            categories=(
                categories
                if isinstance(categories, tuple) and all(isinstance(category, str) for category in categories)
                else None
            ),
            category_status=category_status if isinstance(category_status, str) else "unclassified",
            category_generation=category_generation if isinstance(category_generation, str) else None,
            category_origin=category_origin if isinstance(category_origin, str) else None,
            payload=frozen_payload,
        )

    @staticmethod
    def _freeze_payload(payload: dict) -> Mapping[str, object]:
        return MappingProxyType({key: MemoryCategoryStore._freeze(value) for key, value in payload.items()})

    @staticmethod
    def _freeze(value: object) -> object:
        if isinstance(value, dict):
            return MappingProxyType({key: MemoryCategoryStore._freeze(item) for key, item in value.items()})
        if isinstance(value, (list, tuple)):
            return tuple(MemoryCategoryStore._freeze(item) for item in value)
        if isinstance(value, set):
            return frozenset(MemoryCategoryStore._freeze(item) for item in value)
        return deepcopy(value)
