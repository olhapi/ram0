"""Live PostgreSQL contracts for the cross-process category owner fence."""

import os
import threading
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, delete, event, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from category_models import CategoryDefinition, CategoryJobState, EffectiveCatalog
from category_service import CategoryService
from category_store import CategoryJobStore, MemoryCategoryStore
from models import Base, CategoryJob


_DATABASE_URL_ENV = "RAM0_TEST_POSTGRES_URL"
_TIMEOUT_SECONDS = 5.0
_BLOCK_PROBE_SECONDS = 0.25
_POLL_SECONDS = 0.02
_CATALOG = EffectiveCatalog(
    definitions=(CategoryDefinition(name="integration", description="Live PostgreSQL owner-fence test"),),
    source="request",
)


@dataclass
class _PostgresJobEnvironment:
    engine: Engine
    job_store: CategoryJobStore
    owner_ids: set[uuid.UUID] = field(default_factory=set)

    def new_owner_id(self) -> uuid.UUID:
        owner_id = uuid.uuid4()
        self.owner_ids.add(owner_id)
        return owner_id


class _VectorStore:
    """Small, thread-safe stand-in for the external vector-store boundary."""

    def __init__(self):
        self._rows: dict[str, dict[str, object]] = {}
        self._lock = threading.Lock()

    def create(self, memory_id: str, owner_id: uuid.UUID, text_value: str) -> None:
        with self._lock:
            self._rows[memory_id] = {
                "data": text_value,
                "hash": f"hash-{memory_id}",
                "user_id": str(owner_id),
                "categories": None,
                "category_status": "unclassified",
            }

    def get(self, memory_id: str):
        with self._lock:
            payload = self._rows.get(memory_id)
            return None if payload is None else SimpleNamespace(id=memory_id, payload=deepcopy(payload))

    def _patch_payload(self, memory_id: str, fields: dict[str, object], *, expected: dict[str, object] | None = None):
        with self._lock:
            payload = self._rows.get(memory_id)
            if payload is None or (expected and any(payload.get(key) != value for key, value in expected.items())):
                return None
            payload.update(deepcopy(fields))
            return SimpleNamespace(id=memory_id, payload=deepcopy(payload))


class _OwnerLockWaitProbe:
    """Capture the actual contender backend immediately before it blocks in PostgreSQL."""

    def __init__(self, engine: Engine, contender_thread_name: str):
        self._engine = engine
        self._contender_thread_name = contender_thread_name
        self.backend_pid: int | None = None
        self.lock_query_started = threading.Event()
        event.listen(engine, "before_cursor_execute", self._capture)

    def close(self) -> None:
        event.remove(self._engine, "before_cursor_execute", self._capture)

    def _capture(self, connection, _cursor, statement, _parameters, _context, _executemany) -> None:
        if threading.current_thread().name != self._contender_thread_name or "pg_advisory_lock" not in statement:
            return
        self.backend_pid = connection.connection.driver_connection.info.backend_pid
        self.lock_query_started.set()


@pytest.fixture
def postgres_job_store():
    database_url = os.getenv(_DATABASE_URL_ENV)
    if not database_url:
        pytest.skip(f"set {_DATABASE_URL_ENV} to run live PostgreSQL owner-fence contracts")

    engine = create_engine(
        database_url,
        poolclass=NullPool,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 3},
    )
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        Base.metadata.create_all(engine, tables=[CategoryJob.__table__])
        environment = _PostgresJobEnvironment(
            engine=engine,
            job_store=CategoryJobStore(sessionmaker(bind=engine)),
        )
        yield environment
    finally:
        if "environment" in locals() and environment.owner_ids:
            with sessionmaker(bind=engine)() as session:
                session.execute(delete(CategoryJob).where(CategoryJob.owner_id.in_(environment.owner_ids)))
                session.commit()
        engine.dispose()


def _backend_pid(session) -> int:
    return session.execute(text("SELECT pg_backend_pid()")).scalar_one()


def _job_for_memory(environment: _PostgresJobEnvironment, memory_id: str) -> CategoryJob | None:
    with sessionmaker(bind=environment.engine)() as session:
        return session.execute(select(CategoryJob).where(CategoryJob.memory_id == memory_id)).scalar_one_or_none()


def _wait_for_advisory_lock_wait(engine: Engine, backend_pid: int) -> dict[str, object]:
    """Require PostgreSQL itself to report the identified backend waiting for an advisory lock."""
    deadline = time.monotonic() + _TIMEOUT_SECONDS
    observed: dict[str, object] | None = None
    while time.monotonic() < deadline:
        with engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        """
                    SELECT activity.state, activity.wait_event_type, activity.wait_event,
                           EXISTS (
                               SELECT 1
                               FROM pg_locks lock
                               WHERE lock.pid = activity.pid
                                 AND lock.locktype = 'advisory'
                                 AND NOT lock.granted
                           ) AS waiting_for_advisory_lock
                    FROM pg_stat_activity activity
                    WHERE activity.pid = :backend_pid
                    """
                    ),
                    {"backend_pid": backend_pid},
                )
                .mappings()
                .one_or_none()
            )
        if row is not None:
            observed = dict(row)
            if (
                observed["state"] == "active"
                and observed["wait_event_type"] == "Lock"
                and observed["wait_event"] == "advisory"
                and observed["waiting_for_advisory_lock"] is True
            ):
                return observed
        time.sleep(_POLL_SECONDS)
    pytest.fail(f"backend {backend_pid} did not wait for the advisory lock; last observed state: {observed}")


def _wait_until_contender_blocks(environment: _PostgresJobEnvironment, probe: _OwnerLockWaitProbe) -> int:
    assert probe.lock_query_started.wait(_TIMEOUT_SECONDS)
    assert probe.backend_pid is not None
    _wait_for_advisory_lock_wait(environment.engine, probe.backend_pid)
    return probe.backend_pid


def test_same_owner_connection_blocks_across_memory_creation_and_category_enqueue(postgres_job_store):
    environment = postgres_job_store
    job_store = environment.job_store
    owner_id = environment.new_owner_id()
    memory_id = f"integration-{uuid.uuid4()}"
    vector_store = _VectorStore()
    service = CategoryService(
        object(),
        job_store,
        MemoryCategoryStore(lambda: SimpleNamespace(vector_store=vector_store)),
        object(),
    )
    memory_created = threading.Event()
    allow_enqueue = threading.Event()
    category_enqueued = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()
    second_entered = threading.Event()
    backend_pids: list[int] = []
    enqueue_results: list[dict[str, object]] = []
    durable_jobs: list[CategoryJob] = []
    errors: list[BaseException] = []
    probe = _OwnerLockWaitProbe(environment.engine, "same-owner-contender")

    def first_add_cycle():
        try:
            with job_store.owner_fence(owner_id) as owner_session:
                backend_pids.append(_backend_pid(owner_session))
                vector_store.create(memory_id, owner_id, "Created inside the owner fence")
                memory_created.set()
                if not allow_enqueue.wait(_TIMEOUT_SECONDS):
                    raise TimeoutError("category enqueue phase was not released")
                result = service.after_add(
                    {"results": [{"id": memory_id, "event": "ADD", "memory": "Created inside the owner fence"}]},
                    _CATALOG,
                )
                persisted = _job_for_memory(environment, memory_id)
                if persisted is None:
                    raise AssertionError("after_add did not commit a category job")
                enqueue_results.append(result)
                durable_jobs.append(persisted)
                category_enqueued.set()
                if not release_first.wait(_TIMEOUT_SECONDS):
                    raise TimeoutError("first owner fence was not released")
        except BaseException as error:
            errors.append(error)

    def second_add_cycle():
        try:
            second_started.set()
            with job_store.owner_fence(owner_id) as session:
                backend_pids.append(_backend_pid(session))
                second_entered.set()
        except BaseException as error:
            errors.append(error)

    first = threading.Thread(target=first_add_cycle, daemon=True)
    second = threading.Thread(target=second_add_cycle, name="same-owner-contender", daemon=True)
    first.start()
    try:
        assert memory_created.wait(_TIMEOUT_SECONDS)
        second.start()
        assert second_started.wait(_TIMEOUT_SECONDS)
        contender_backend_pid = _wait_until_contender_blocks(environment, probe)
        assert not second_entered.wait(_BLOCK_PROBE_SECONDS)

        allow_enqueue.set()
        assert category_enqueued.wait(_TIMEOUT_SECONDS)
        _wait_for_advisory_lock_wait(environment.engine, contender_backend_pid)
        assert not second_entered.wait(_BLOCK_PROBE_SECONDS)
    finally:
        allow_enqueue.set()
        release_first.set()
        first.join(_TIMEOUT_SECONDS)
        if second.ident is not None:
            second.join(_TIMEOUT_SECONDS)
        probe.close()

    assert second_entered.is_set()
    assert not first.is_alive()
    assert not second.is_alive()
    assert errors == []
    assert len(set(backend_pids)) == 2
    assert contender_backend_pid in backend_pids
    assert enqueue_results[0]["results"][0]["category_status"] == "pending"
    assert enqueue_results[0]["results"][0]["categories"] is None
    assert durable_jobs[0].owner_id == owner_id
    assert durable_jobs[0].memory_id == memory_id
    assert durable_jobs[0].state == CategoryJobState.QUEUED


def test_owner_fence_releases_after_error_and_rollback(postgres_job_store):
    environment = postgres_job_store
    job_store = environment.job_store
    owner_id = environment.new_owner_id()
    first_entered = threading.Event()
    raise_error = threading.Event()
    second_started = threading.Event()
    second_entered = threading.Event()
    backend_pids: list[int] = []
    errors: list[BaseException] = []
    probe = _OwnerLockWaitProbe(environment.engine, "error-owner-contender")

    class ExpectedFailure(RuntimeError):
        pass

    def failing_owner_mutation():
        try:
            with job_store.owner_fence(owner_id) as session:
                backend_pids.append(_backend_pid(session))
                first_entered.set()
                if not raise_error.wait(_TIMEOUT_SECONDS):
                    raise TimeoutError("error path was not released")
                raise ExpectedFailure("force owner-fence rollback")
        except ExpectedFailure:
            pass
        except BaseException as error:
            errors.append(error)

    def waiting_owner_mutation():
        try:
            second_started.set()
            with job_store.owner_fence(owner_id) as session:
                backend_pids.append(_backend_pid(session))
                second_entered.set()
        except BaseException as error:
            errors.append(error)

    first = threading.Thread(target=failing_owner_mutation, daemon=True)
    second = threading.Thread(target=waiting_owner_mutation, name="error-owner-contender", daemon=True)
    first.start()
    try:
        assert first_entered.wait(_TIMEOUT_SECONDS)
        second.start()
        assert second_started.wait(_TIMEOUT_SECONDS)
        contender_backend_pid = _wait_until_contender_blocks(environment, probe)
        assert not second_entered.wait(_BLOCK_PROBE_SECONDS)
    finally:
        raise_error.set()
        first.join(_TIMEOUT_SECONDS)
        if second.ident is not None:
            second.join(_TIMEOUT_SECONDS)
        probe.close()

    assert second_entered.is_set()
    assert not first.is_alive()
    assert not second.is_alive()
    assert errors == []
    assert len(set(backend_pids)) == 2
    assert contender_backend_pid in backend_pids


def test_different_owner_connections_proceed_independently(postgres_job_store):
    environment = postgres_job_store
    job_store = environment.job_store
    first_owner_id = environment.new_owner_id()
    second_owner_id = environment.new_owner_id()
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()
    backend_pids: list[int] = []
    errors: list[BaseException] = []

    def hold_first_owner():
        try:
            with job_store.owner_fence(first_owner_id) as session:
                backend_pids.append(_backend_pid(session))
                first_entered.set()
                if not release_first.wait(_TIMEOUT_SECONDS):
                    raise TimeoutError("first owner fence was not released")
        except BaseException as error:
            errors.append(error)

    def enter_second_owner():
        try:
            with job_store.owner_fence(second_owner_id) as session:
                backend_pids.append(_backend_pid(session))
                second_entered.set()
        except BaseException as error:
            errors.append(error)

    first = threading.Thread(target=hold_first_owner, daemon=True)
    second = threading.Thread(target=enter_second_owner, daemon=True)
    first.start()
    try:
        assert first_entered.wait(_TIMEOUT_SECONDS)
        second.start()
        assert second_entered.wait(_TIMEOUT_SECONDS)
    finally:
        release_first.set()
        first.join(_TIMEOUT_SECONDS)
        if second.ident is not None:
            second.join(_TIMEOUT_SECONDS)

    assert not first.is_alive()
    assert not second.is_alive()
    assert errors == []
    assert len(set(backend_pids)) == 2
