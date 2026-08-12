"""Database-free contract tests for category catalog and job persistence."""

import json
import threading
import uuid
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, event
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

from category_models import CategoryDefinition, CategoryJobState
from category_store import (
    CategoryCatalogStore,
    CategoryCatalogStoreError,
    CategoryJobStore,
    EnqueueResult,
    MemoryCategoryStore,
    MemorySnapshot,
)
from models import Base, CategoryJob, Settings


CATALOG = (
    CategoryDefinition(name="billing", description="Invoices"),
    CategoryDefinition(name="support", description="Cases"),
)
NOW = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
OWNER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
OWNER_B_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")
_CATEGORY_JOB_MODEL = CategoryJob


def owned_job(**kwargs):
    """Build a durable job with the default test owner unless explicitly overridden."""
    kwargs.setdefault("owner_id", OWNER_ID)
    return _CATEGORY_JOB_MODEL(**kwargs)


def scalar_result(value):
    """Return the smallest SQLAlchemy scalar-result double needed by a store test."""
    result = MagicMock(name="scalar_result")
    result.scalar_one_or_none.return_value = value
    return result


def scalar_rows(*values):
    """Return the smallest SQLAlchemy scalars-result double for a candidate batch."""
    result = MagicMock(name="scalar_rows")
    result.scalars.return_value.all.return_value = list(values)
    return result


@pytest.fixture
def catalog_store(session_factory):
    session = session_factory.return_value

    session_factory.row_by_key = {}

    def remember(row):
        session_factory.row = row
        session_factory.row_by_key[row.key] = row

    session.add.side_effect = remember
    session.get.side_effect = lambda _model, key, **_kwargs: session_factory.row_by_key.get(
        key,
        session_factory.__dict__.get("row") if key == "custom_categories" else None,
    )
    return CategoryCatalogStore(session_factory)


@pytest.fixture
def job_store(session_factory):
    session = session_factory.return_value
    jobs = {}

    def persist(job):
        if job.id is None:
            job.id = uuid.uuid4()
        if job.state is None:
            job.state = CategoryJobState.QUEUED
        if job.attempts is None:
            job.attempts = 0
        jobs[job.id] = job

    session.add.side_effect = persist
    session.get.side_effect = lambda _model, job_id: jobs.get(job_id)
    return CategoryJobStore(session_factory)


def row(payload, memory_id="mem-1", *, add_owner=True):
    """Build the object shape returned by the pinned PGVector store."""
    if add_owner:
        payload.setdefault("user_id", str(OWNER_ID))
    return SimpleNamespace(id=memory_id, payload=payload)


@pytest.fixture
def vector_store():
    return MagicMock(name="vector_store")


@pytest.fixture
def memory_store(vector_store):
    memory = SimpleNamespace(vector_store=vector_store)
    return MemoryCategoryStore(lambda: memory)


def test_mark_pending_preserves_unrelated_payload_and_vector(memory_store, vector_store):
    vector_store._patch_payload.return_value = row(
        {
            "data": "Invoice",
            "hash": "h1",
            "user_id": str(OWNER_ID),
            "custom": 7,
            "concurrent_metadata": "preserved",
            "categories": None,
            "category_status": "pending",
            "_category_generation": "job-1",
        }
    )

    snapshot = memory_store.mark_pending("mem-1", "job-1", owner_id=OWNER_ID)

    assert snapshot is not None
    assert snapshot.memory_hash == "h1"
    assert snapshot.payload["concurrent_metadata"] == "preserved"
    vector_store._patch_payload.assert_called_once_with(
        "mem-1",
        {
            "categories": None,
            "category_status": "pending",
            "_category_generation": "job-1",
        },
        expected={"user_id": str(OWNER_ID)},
    )
    vector_store.get.assert_not_called()
    vector_store.update.assert_not_called()


def test_replacement_generation_blocks_a_renewed_old_worker_before_new_job_enqueue():
    class AtomicVectorStore:
        def __init__(self):
            self.payload = {
                "data": "Same text",
                "hash": "h1",
                "user_id": str(OWNER_ID),
                "categories": None,
                "category_status": "pending",
                "_category_generation": "job-a",
            }

        def _patch_payload(self, vector_id, fields, *, expected=None):
            assert vector_id == "mem-1"
            if expected and any(self.payload.get(key) != value for key, value in expected.items()):
                return None
            self.payload.update(fields)
            return row(dict(self.payload))

    vector_store = AtomicVectorStore()
    store = MemoryCategoryStore(lambda: SimpleNamespace(vector_store=vector_store))

    replacement = store.mark_pending("mem-1", "job-b", owner_id=OWNER_ID)
    old_write = store.write_result(
        "mem-1", "h1", "job-a", ["billing"], "completed", owner_id=OWNER_ID
    )

    assert replacement is not None
    assert replacement.category_generation == "job-b"
    assert old_write is False
    assert vector_store.payload["category_status"] == "pending"
    assert vector_store.payload["_category_generation"] == "job-b"


def test_staged_job_restart_can_bind_its_generation_only_to_the_same_hash(memory_store, vector_store):
    vector_store._patch_payload.side_effect = [
        row({"data": "Invoice", "hash": "h1", "_category_generation": "job-a", "category_status": "pending"}),
        None,
    ]

    current = memory_store.mark_pending("mem-1", "job-a", owner_id=OWNER_ID, expected_hash="h1")
    stale = memory_store.mark_pending("mem-1", "job-b", owner_id=OWNER_ID, expected_hash="old-hash")

    assert current is not None
    assert stale is None
    assert vector_store._patch_payload.call_args_list[0].kwargs["expected"] == {
        "user_id": str(OWNER_ID),
        "hash": "h1",
    }


def test_recovery_binding_requires_generation_to_still_be_null(memory_store, vector_store):
    """A legacy recovery worker cannot replace a newer generation installed after its read."""
    vector_store._patch_payload.return_value = None

    assert memory_store.mark_pending(
        "mem-1",
        "legacy-job",
        owner_id=OWNER_ID,
        expected_hash="h1",
        expected_text="Invoice",
        expected_generation=None,
    ) is None

    assert vector_store._patch_payload.call_args.kwargs["expected"] == {
        "user_id": str(OWNER_ID),
        "hash": "h1",
        "data": "Invoice",
        "_category_generation": None,
    }


def test_write_result_rejects_changed_hash(memory_store, vector_store):
    vector_store._patch_payload.return_value = None

    assert memory_store.write_result(
        "mem-1", "h1", "job-1", ["billing"], "completed", owner_id=OWNER_ID
    ) is False

    vector_store._patch_payload.assert_called_once_with(
        "mem-1",
        {
            "categories": ["billing"],
            "category_status": "completed",
            "_category_generation": None,
        },
        expected={
            "user_id": str(OWNER_ID),
            "hash": "h1",
            "_category_generation": "job-1",
            "_category_origin": None,
        },
    )
    vector_store.get.assert_not_called()
    vector_store.update.assert_not_called()


def test_write_result_rejects_a_deleted_memory(memory_store, vector_store):
    vector_store._patch_payload.return_value = None

    assert memory_store.write_result(
        "mem-1", "h1", "job-1", ["billing"], "completed", owner_id=OWNER_ID
    ) is False


def test_fail_origin_clears_only_the_exact_unrecoverable_request_marker(memory_store, vector_store):
    current = MemorySnapshot(
        memory_id="mem-1",
        user_id=OWNER_ID,
        text="Same",
        memory_hash="h1",
        categories=None,
        category_status="unclassified",
        category_generation=None,
        category_origin="origin-1",
        payload={},
    )
    vector_store._patch_payload.return_value = row({"data": "Same", "hash": "h1"})

    assert memory_store.fail_origin(current) is True

    vector_store._patch_payload.assert_called_once_with(
        "mem-1",
        {
            "categories": [],
            "category_status": "failed",
            "_category_generation": None,
            "_category_origin": None,
        },
        expected={
            "user_id": str(OWNER_ID),
            "hash": "h1",
            "_category_generation": None,
            "_category_origin": "origin-1",
        },
    )

    vector_store.update.assert_not_called()


def test_clear_origin_preserves_a_foreign_generation(memory_store, vector_store):
    current = MemorySnapshot(
        memory_id="mem-1",
        user_id=OWNER_ID,
        text="Same",
        memory_hash="h1",
        categories=None,
        category_status="pending",
        category_generation="newer-job",
        category_origin="old-origin",
        payload={},
    )
    vector_store._patch_payload.return_value = row({"data": "Same", "hash": "h1"})

    assert memory_store.clear_origin(current) is True

    vector_store._patch_payload.assert_called_once_with(
        "mem-1",
        {"_category_origin": None},
        expected={
            "user_id": str(OWNER_ID),
            "hash": "h1",
            "_category_generation": "newer-job",
            "_category_origin": "old-origin",
        },
    )


def test_write_result_returns_snapshot_from_atomic_patch_without_overwriting_concurrent_fields(
    memory_store, vector_store
):
    vector_store._patch_payload.return_value = row(
        {
            "data": "Invoice",
            "hash": "h1",
            "metadata_changed_concurrently": True,
            "categories": ["billing"],
            "category_status": "completed",
        }
    )

    assert memory_store.write_result(
        "mem-1", "h1", "job-1", ["billing"], "completed", owner_id=OWNER_ID
    ) is True

    vector_store._patch_payload.assert_called_once_with(
        "mem-1",
        {
            "categories": ["billing"],
            "category_status": "completed",
            "_category_generation": None,
        },
        expected={
            "user_id": str(OWNER_ID),
            "hash": "h1",
            "_category_generation": "job-1",
            "_category_origin": None,
        },
    )


def test_restore_reverts_only_category_fields_owned_by_failed_prepared_install(memory_store, vector_store):
    previous = MemorySnapshot(
        memory_id="mem-1",
        user_id=OWNER_ID,
        text="Invoice",
        memory_hash="h1",
        categories=("billing",),
        category_status="completed",
        category_generation=None,
        payload={"source": "preserved"},
    )
    vector_store._patch_payload.return_value = row(
        {
            "data": "Invoice",
            "hash": "h1",
            "source": "concurrent",
            "categories": ["billing"],
            "category_status": "completed",
            "_category_generation": None,
        }
    )

    assert memory_store.restore(previous, expected_generation="prepared-job") is True
    vector_store._patch_payload.assert_called_once_with(
        "mem-1",
        {
            "categories": ["billing"],
            "category_status": "completed",
            "_category_generation": None,
        },
        expected={
            "user_id": str(OWNER_ID),
            "hash": "h1",
            "_category_generation": "prepared-job",
            "_category_origin": None,
        },
    )


def test_get_returns_unclassified_legacy_payload(memory_store, vector_store):
    vector_store.get.return_value = row({"data": "Invoice", "hash": "h1", "custom": 7})

    snapshot = memory_store.get("mem-1")

    assert snapshot is not None
    assert snapshot.memory_id == "mem-1"
    assert snapshot.text == "Invoice"
    assert snapshot.memory_hash == "h1"
    assert snapshot.categories is None
    assert snapshot.category_status == "unclassified"
    assert snapshot.payload == {
        "data": "Invoice",
        "hash": "h1",
        "custom": 7,
        "user_id": str(OWNER_ID),
    }


def test_get_returns_a_recursively_frozen_snapshot_detached_from_the_vector_row(memory_store, vector_store):
    source_payload = {
        "data": "Invoice",
        "hash": "h1",
        "categories": ["billing"],
        "metadata": {"labels": ["urgent"], "flags": {"active"}},
    }
    source_row = row(source_payload)
    vector_store.get.return_value = source_row

    snapshot = memory_store.get("mem-1")

    assert snapshot is not None
    assert snapshot.categories == ("billing",)
    with pytest.raises(TypeError):
        snapshot.categories[0] = "support"
    with pytest.raises(TypeError):
        snapshot.payload["data"] = "Changed"
    with pytest.raises(TypeError):
        snapshot.payload["metadata"]["labels"][0] = "later"
    with pytest.raises(AttributeError):
        snapshot.payload["metadata"]["flags"].add("archived")
    assert source_row.payload == {
        "data": "Invoice",
        "hash": "h1",
        "user_id": str(OWNER_ID),
        "categories": ["billing"],
        "metadata": {"labels": ["urgent"], "flags": {"active"}},
    }


def test_iter_snapshots_flattens_pgvector_rows_and_counts_retired_labels(memory_store, vector_store):
    vector_store.list.return_value = [
        [
            row({"data": "Invoice", "categories": ["billing", "retired"]}, "mem-1"),
            row({"data": "Case", "categories": ["retired"]}, "mem-2"),
        ]
    ]

    snapshots = list(memory_store.iter_snapshots(str(OWNER_ID)))

    assert [snapshot.memory_id for snapshot in snapshots] == ["mem-1", "mem-2"]
    assert memory_store.category_counts(str(OWNER_ID)) == {"billing": 1, "retired": 2}
    assert vector_store.list.call_args.kwargs["filters"] == {"user_id": str(OWNER_ID)}
    vector_store.list.assert_called_with(filters={"user_id": str(OWNER_ID)}, top_k=None)


@pytest.mark.parametrize("payload_owner", [None, "not-a-uuid"])
def test_snapshot_boundary_quarantines_missing_or_malformed_owner(
    memory_store, vector_store, caplog, payload_owner
):
    payload = {"data": "Private", "categories": []}
    if payload_owner is not None:
        payload["user_id"] = payload_owner
    vector_store.list.return_value = [[row(payload, "memory-invalid-owner", add_owner=False)]]

    assert list(memory_store.iter_snapshots(str(OWNER_ID))) == []
    assert "category_memory_owner_invalid" in caplog.text
    assert "memory_id=memory-invalid-owner" in caplog.text


def test_catalog_round_trip_preserves_order(catalog_store, session_factory):
    saved = catalog_store.replace(str(OWNER_ID), CATALOG)

    assert [item.name for item in saved] == ["billing", "support"]
    assert json.loads(session_factory.row.value) == [
        {"name": "billing", "description": "Invoices"},
        {"name": "support", "description": "Cases"},
    ]
    assert catalog_store.get_saved(str(OWNER_ID)) == CATALOG
    assert session_factory.return_value.get.call_args.args[1] == f"custom_categories:{OWNER_ID}"


def test_first_owner_read_copies_the_validated_legacy_catalog_once(catalog_store, session_factory):
    """Mutating the legacy template would make a new account observe another account's catalog."""
    session_factory.row_by_key["custom_categories"] = Settings(
        key="custom_categories", value='[{"name":"billing","description":"Invoices"}]'
    )

    assert catalog_store.get_saved(str(OWNER_ID)) == (CategoryDefinition(name="billing", description="Invoices"),)

    assert session_factory.row_by_key[f"custom_categories:{OWNER_ID}"].value == '[{"name":"billing","description":"Invoices"}]'
    assert session_factory.row_by_key["custom_categories"].value == '[{"name":"billing","description":"Invoices"}]'


def test_legacy_catalog_remains_template_after_two_owner_initializations(catalog_store, session_factory):
    """Writing one owner's copy must not change the legacy template for the next owner."""
    billing = (CategoryDefinition(name="billing", description="Invoices"),)
    support = (CategoryDefinition(name="support", description="Cases"),)
    session_factory.row_by_key["custom_categories"] = Settings(
        key="custom_categories", value='[{"name":"billing","description":"Invoices"}]'
    )

    first = catalog_store.get_saved(str(OWNER_ID))
    catalog_store.replace(str(OWNER_ID), support)
    second = catalog_store.get_saved(str(OWNER_B_ID))

    assert first == second == billing


def test_catalog_changes_for_one_owner_do_not_change_another(catalog_store):
    """Sharing the settings row would make one account's edits alter another account's future labels."""
    support = (CategoryDefinition(name="support", description="Cases"),)

    catalog_store.replace(str(OWNER_ID), CATALOG)
    catalog_store.replace(str(OWNER_B_ID), support)

    assert catalog_store.get_saved(str(OWNER_ID)) == CATALOG
    assert catalog_store.get_saved(str(OWNER_B_ID)) == support


def test_catalog_empty_replacement_persists_an_empty_json_array(catalog_store, session_factory):
    assert catalog_store.replace(str(OWNER_ID), ()) == ()
    assert session_factory.row.value == "[]"


def test_catalog_rejects_corrupt_json_without_falling_back_to_labels(catalog_store, session_factory, caplog):
    session_factory.row = MagicMock(value="not json")

    with pytest.raises(CategoryCatalogStoreError, match="stored category catalog"):
        catalog_store.get_saved(str(OWNER_ID))

    assert "Failed to load stored category catalog" in caplog.messages


def test_catalog_create_locks_the_project_catalog_and_mutates_in_one_transaction(catalog_store, session_factory):
    session = session_factory.return_value
    row_value = Settings(key="custom_categories", value=json.dumps([CATALOG[0].model_dump()]))
    session_factory.row = row_value

    saved = catalog_store.create(str(OWNER_ID), CATALOG[1])

    advisory_statement = str(session.execute.call_args_list[0].args[0])
    assert "pg_advisory_xact_lock" in advisory_statement
    assert session.get.call_args_list[0].kwargs == {"with_for_update": True}
    assert saved == CATALOG
    assert json.loads(session_factory.row_by_key[f"custom_categories:{OWNER_ID}"].value) == [
        definition.model_dump() for definition in CATALOG
    ]
    session.commit.assert_called_once_with()


def test_catalog_partial_update_preserves_fields_loaded_under_the_transaction_lock(catalog_store, session_factory):
    latest = CategoryDefinition(name="billing", description="Description changed by another admin.")
    row_value = Settings(key="custom_categories", value=json.dumps([latest.model_dump(), CATALOG[1].model_dump()]))
    session_factory.row = row_value

    saved = catalog_store.update(str(OWNER_ID), "billing", new_name="invoices")

    assert saved[0] == CategoryDefinition(name="invoices", description=latest.description)
    assert saved[1] == CATALOG[1]


def test_catalog_delete_mutates_the_locked_latest_catalog(catalog_store, session_factory):
    row_value = Settings(key="custom_categories", value=json.dumps([item.model_dump() for item in CATALOG]))
    session_factory.row = row_value

    saved = catalog_store.delete(str(OWNER_ID), "billing")

    assert saved == (CATALOG[1],)
    assert json.loads(session_factory.row_by_key[f"custom_categories:{OWNER_ID}"].value) == [CATALOG[1].model_dump()]


def test_concurrent_catalog_creates_serialize_without_losing_either_admin_update():
    class SharedCatalog:
        def __init__(self):
            self.lock = threading.Lock()
            self.row = Settings(key="custom_categories", value=json.dumps([CATALOG[0].model_dump()]))

    class LockedSession:
        def __init__(self, shared):
            self.shared = shared
            self.owns_lock = False

        def execute(self, statement, _params):
            assert "pg_advisory_xact_lock" in str(statement)
            self.shared.lock.acquire()
            self.owns_lock = True

        def get(self, _model, _key, *, with_for_update=False):
            return self.shared.row

        def add(self, row_value):
            self.shared.row = row_value

        def commit(self):
            self.shared.lock.release()
            self.owns_lock = False

        def rollback(self):
            if self.owns_lock:
                self.shared.lock.release()
                self.owns_lock = False

        def close(self):
            self.rollback()

    shared = SharedCatalog()
    store = CategoryCatalogStore(lambda: LockedSession(shared))
    additions = (
        CategoryDefinition(name="legal", description="Legal matters"),
        CategoryDefinition(name="security", description="Security matters"),
    )
    barrier = threading.Barrier(3)
    errors = []

    def create(definition):
        try:
            barrier.wait()
            store.create(str(OWNER_ID), definition)
        except Exception as error:  # pragma: no cover - asserted below with thread-safe append
            errors.append(error)

    threads = [threading.Thread(target=create, args=(definition,)) for definition in additions]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=1)

    assert errors == []
    assert not any(thread.is_alive() for thread in threads)
    assert {item["name"] for item in json.loads(shared.row.value)} == {"billing", "legal", "security"}


def test_memory_fence_holds_one_session_advisory_lock_across_commits(job_store, session_factory):
    session = session_factory.return_value

    with job_store.memory_fence("mem-1") as fenced:
        assert fenced is session
        assert session.commit.call_count == 1

    lock_call, unlock_call = session.execute.call_args_list
    statement, params = lock_call.args
    assert "pg_advisory_lock" in str(statement)
    assert params == {"lock_key": "category-memory:mem-1"}
    assert "pg_advisory_unlock" in str(unlock_call.args[0])
    assert session.commit.call_count == 2
    session.close.assert_called_once_with()


def test_owner_fence_holds_a_cross_process_advisory_lock(job_store, session_factory):
    session = session_factory.return_value

    with job_store.owner_fence(OWNER_ID) as fenced:
        assert fenced is session
        assert session.commit.call_count == 1

    lock_call, unlock_call = session.execute.call_args_list
    statement, params = lock_call.args
    assert "pg_advisory_lock" in str(statement)
    assert params == {"lock_key": f"category-owner:{OWNER_ID}"}
    assert "pg_advisory_unlock" in str(unlock_call.args[0])
    assert session.commit.call_count == 2
    session.close.assert_called_once_with()


def test_owner_then_memory_fence_reuses_one_session_and_lock_order(job_store, session_factory):
    """Owner-first nesting avoids pool re-entry and prevents lock-order inversion."""
    session = session_factory.return_value

    with job_store.owner_fence(OWNER_ID) as owner_session:
        with job_store.memory_fence("mem-1") as memory_session:
            assert memory_session is owner_session

    assert session_factory.call_count == 1
    assert [call.args[1] for call in session.execute.call_args_list] == [
        {"lock_key": f"category-owner:{OWNER_ID}"},
        {"lock_key": "category-memory:mem-1"},
        {"lock_key": "category-memory:mem-1"},
        {"lock_key": f"category-owner:{OWNER_ID}"},
    ]


def test_try_memory_fence_uses_postgres_nonblocking_lock_and_does_not_unlock_when_busy(
    job_store, session_factory
):
    session = session_factory.return_value
    result = MagicMock(name="try_lock_result")
    result.scalar_one.return_value = False
    session.execute.return_value = result

    with job_store.try_memory_fence("mem-busy") as fenced:
        assert fenced is None

    assert session.execute.call_count == 1
    statement, params = session.execute.call_args.args
    assert "pg_try_advisory_lock" in str(statement)
    assert params == {"lock_key": "category-memory:mem-busy"}
    session.commit.assert_called_once_with()
    session.close.assert_called_once_with()


def test_memory_fence_threads_its_only_session_into_nested_job_operations(session_factory):
    """A pool with one connection must not deadlock on a second session checkout."""
    store = CategoryJobStore(session_factory)
    job_id = uuid.uuid4()
    session_factory.return_value.get.return_value = None

    with store.memory_fence("mem-1") as session:
        store.prepare("mem-1", "hash-1", CATALOG, job_id=job_id, owner_id=OWNER_ID, session=session)

    assert session_factory.call_count == 1


def test_memory_fence_does_not_self_deadlock_with_a_real_pool_of_one():
    @compiles(JSONB, "sqlite")
    def compile_jsonb_as_json(_type, _compiler, **_kwargs):
        return "JSON"

    engine = create_engine(
        "sqlite://",
        poolclass=QueuePool,
        pool_size=1,
        max_overflow=0,
        pool_timeout=0.1,
    )

    @event.listens_for(engine, "connect")
    def register_advisory_functions(connection, _record):
        connection.create_function("hashtextextended", 2, lambda _key, _seed: 1)
        connection.create_function("pg_advisory_lock", 1, lambda _key: 1)
        connection.create_function("pg_try_advisory_lock", 1, lambda _key: 1)
        connection.create_function("pg_advisory_unlock", 1, lambda _key: 1)

    Base.metadata.create_all(engine, tables=[CategoryJob.__table__])
    store = CategoryJobStore(sessionmaker(bind=engine))
    job_id = uuid.uuid4()

    with store.memory_fence("mem-1") as session:
        result = store.prepare("mem-1", "hash-1", CATALOG, job_id=job_id, owner_id=OWNER_ID, session=session)

    assert result.job_id == job_id
    assert store.list_prepared()[0].id == job_id
    assert store.list_prepared()[0].owner_id == OWNER_ID
    assert store.list_jobs(owner_id=OWNER_ID) == []
    with store.memory_fence("mem-1") as session:
        assert store.install_prepared(job_id, OWNER_ID, session=session) is True
    claimed = store.claim(
        "worker-1",
        datetime(2999, 1, 1),
        lease_seconds=30,
    )
    assert claimed is not None
    assert claimed.id == job_id
    engine.dispose()


def test_preparing_is_not_a_public_job_state():
    assert "preparing" not in {state.value for state in CategoryJobState}


def test_prepare_persists_catalog_without_cancelling_active_job(job_store, session_factory):
    session = session_factory.return_value
    active = owned_job(
        id=uuid.uuid4(),
        memory_id="mem-1",
        memory_hash="hash-old",
        catalog_snapshot=[],
        state=CategoryJobState.PROCESSING,
    )
    session.execute.return_value = scalar_result(active)
    job_id = uuid.uuid4()

    prepared = job_store.prepare("mem-1", "hash-new", CATALOG, job_id=job_id, owner_id=OWNER_ID)

    assert prepared == EnqueueResult(job_id=job_id, created=True)
    assert job_store.get(job_id).state == "preparing"
    assert active.state == CategoryJobState.PROCESSING


def test_prepare_revives_an_exact_cancelled_origin_tombstone(job_store, session_factory):
    job_id = uuid.uuid4()
    cancelled = owned_job(
        id=job_id,
        memory_id="mem-1",
        owner_id=OWNER_ID,
        memory_hash="hash-new",
        catalog_snapshot=[definition.model_dump() for definition in CATALOG],
        state=CategoryJobState.CANCELLED,
        error_code="replaced",
        completed_at=NOW,
    )
    session_factory.return_value.get.side_effect = None
    session_factory.return_value.get.return_value = cancelled

    result = job_store.prepare("mem-1", "hash-new", CATALOG, job_id=job_id, owner_id=OWNER_ID)

    assert result.created is True
    assert cancelled.state == "preparing"
    assert cancelled.error_code is None
    assert cancelled.completed_at is None


@pytest.mark.parametrize(
    "newer_state",
    [CategoryJobState.PROCESSING, CategoryJobState.COMPLETED],
    ids=["active", "completed"],
)
def test_preparation_is_latest_only_when_no_newer_job_row_exists(
    job_store, session_factory, newer_state
):
    preparation = owned_job(
        id=uuid.uuid4(),
        memory_id="mem-1",
        memory_hash="h1",
        catalog_snapshot=[],
        state="preparing",
        created_at=NOW,
    )
    newer = owned_job(
        id=uuid.uuid4(),
        memory_id="mem-1",
        memory_hash="h1",
        catalog_snapshot=[],
        state=newer_state,
        created_at=NOW + timedelta(seconds=1),
    )
    session_factory.return_value.execute.side_effect = [scalar_result(preparation), scalar_result(newer)]

    assert job_store.preparation_is_latest(preparation.id, "mem-1", OWNER_ID) is False


def test_install_prepared_cancels_active_and_queues_catalog_atomically(job_store, session_factory):
    session = session_factory.return_value
    prepared = owned_job(
        id=uuid.uuid4(),
        memory_id="mem-1",
        memory_hash="hash-new",
        catalog_snapshot=[{"name": "billing", "description": "Invoices"}],
        state="preparing",
    )
    active = owned_job(
        id=uuid.uuid4(),
        memory_id="mem-1",
        memory_hash="hash-old",
        catalog_snapshot=[],
        state=CategoryJobState.PROCESSING,
    )
    session.execute.side_effect = [scalar_result(prepared), scalar_result(active)]

    assert job_store.install_prepared(prepared.id, OWNER_ID) is True
    assert active.state == CategoryJobState.CANCELLED
    assert prepared.state == CategoryJobState.QUEUED
    session.commit.assert_called_once_with()


def test_install_prepared_is_idempotent_after_an_ambiguous_successful_commit(job_store, session_factory):
    installed = owned_job(
        id=uuid.uuid4(),
        memory_id="mem-1",
        memory_hash="hash-new",
        catalog_snapshot=[{"name": "billing", "description": "Invoices"}],
        state=CategoryJobState.QUEUED,
    )
    session_factory.return_value.execute.return_value = scalar_result(installed)

    assert job_store.install_prepared(installed.id, OWNER_ID) is True
    session_factory.return_value.commit.assert_not_called()


def test_install_prepared_rejects_a_terminal_or_missing_reservation(job_store, session_factory):
    terminal = owned_job(
        id=uuid.uuid4(),
        memory_id="mem-1",
        catalog_snapshot=[],
        state=CategoryJobState.CANCELLED,
    )
    session_factory.return_value.execute.return_value = scalar_result(terminal)

    assert job_store.install_prepared(terminal.id, OWNER_ID) is False
    session_factory.return_value.commit.assert_not_called()


def test_active_match_requires_job_id_and_hash(job_store, session_factory):
    active = owned_job(
        id=uuid.uuid4(),
        memory_id="mem-1",
        owner_id=OWNER_ID,
        memory_hash="hash-1",
        catalog_snapshot=[],
        state=CategoryJobState.PROCESSING,
    )
    session_factory.return_value.execute.return_value = scalar_result(active)

    assert job_store.active_matches("mem-1", "hash-1", str(active.id), owner_id=OWNER_ID) is True
    assert job_store.active_matches("mem-1", "other-hash", str(active.id), owner_id=OWNER_ID) is False
    assert job_store.active_matches("mem-1", "hash-1", str(uuid.uuid4()), owner_id=OWNER_ID) is False
    assert (
        job_store.active_matches(
            "mem-1",
            "hash-1",
            str(active.id),
            owner_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
        )
        is False
    )


def test_claim_fences_before_row_lock_and_returns_an_immutable_snapshot(job_store, session_factory):
    session = session_factory.return_value
    queued = owned_job(
        id=uuid.uuid4(),
        memory_id="mem-1",
        owner_id=OWNER_ID,
        memory_hash="hash-1",
        catalog_snapshot=[{"name": "billing", "description": "Invoices"}],
        state=CategoryJobState.QUEUED,
        created_at=NOW,
        next_attempt_at=NOW,
        attempts=0,
    )
    session.execute.side_effect = [scalar_rows(queued), scalar_result(queued)]
    job_store.try_memory_fence = MagicMock(return_value=nullcontext(session))

    claimed = job_store.claim("worker-1", NOW, lease_seconds=30)

    candidate_statement = session.execute.call_args_list[0].args[0]
    locked_statement = session.execute.call_args_list[1].args[0]
    assert "FOR UPDATE" not in str(candidate_statement.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" in str(locked_statement.compile(dialect=postgresql.dialect()))
    assert claimed is not None
    assert claimed.memory_id == "mem-1"
    assert claimed.owner_id == OWNER_ID
    assert claimed.catalog == (CategoryDefinition(name="billing", description="Invoices"),)
    assert queued.state == CategoryJobState.PROCESSING
    assert queued.worker_id == "worker-1"
    assert queued.attempts == 1
    assert queued.lease_expires_at == NOW + timedelta(seconds=30)
    with pytest.raises(ValidationError, match="Instance is frozen"):
        claimed.worker_id = "worker-2"
    session.commit.assert_called_once_with()


def test_claim_selects_candidate_then_takes_memory_fence_before_row_lock(job_store, session_factory):
    session = session_factory.return_value
    candidate = owned_job(
        id=uuid.uuid4(),
        memory_id="mem-1",
        memory_hash="h1",
        catalog_snapshot=[{"name": "billing", "description": "Invoices"}],
        state=CategoryJobState.QUEUED,
        created_at=NOW,
        next_attempt_at=NOW,
        attempts=0,
    )
    session.execute.side_effect = [scalar_rows(candidate), scalar_result(candidate)]
    job_store.try_memory_fence = MagicMock(return_value=nullcontext(session))

    assert job_store.claim("worker-1", NOW, lease_seconds=30) is not None

    first_sql = str(session.execute.call_args_list[0].args[0].compile(dialect=postgresql.dialect()))
    second_sql = str(session.execute.call_args_list[1].args[0].compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" not in first_sql
    assert "FOR UPDATE" in second_sql
    job_store.try_memory_fence.assert_called_once_with("mem-1")


def test_claim_skips_a_busy_oldest_memory_and_immediately_claims_unrelated_work(
    job_store, session_factory
):
    session = session_factory.return_value
    oldest = owned_job(
        id=uuid.uuid4(),
        memory_id="mem-busy",
        memory_hash="h1",
        catalog_snapshot=[{"name": "billing", "description": "Invoices"}],
        state=CategoryJobState.QUEUED,
        created_at=NOW,
        next_attempt_at=NOW,
        attempts=0,
    )
    ready = owned_job(
        id=uuid.uuid4(),
        memory_id="mem-ready",
        memory_hash="h2",
        catalog_snapshot=[{"name": "support", "description": "Cases"}],
        state=CategoryJobState.QUEUED,
        created_at=NOW + timedelta(seconds=1),
        next_attempt_at=NOW,
        attempts=0,
    )
    session.execute.side_effect = [scalar_rows(oldest, ready), scalar_result(ready)]
    job_store.try_memory_fence = MagicMock(
        side_effect=[nullcontext(None), nullcontext(session)]
    )

    claimed = job_store.claim("worker-1", NOW, lease_seconds=30)

    assert claimed is not None
    assert claimed.id == ready.id
    assert [call.args[0] for call in job_store.try_memory_fence.call_args_list] == [
        "mem-busy",
        "mem-ready",
    ]


def test_claim_keyset_pages_past_sixteen_busy_memories_to_ready_job(
    job_store, session_factory
):
    session = session_factory.return_value
    busy = [
        owned_job(
            id=uuid.UUID(int=index + 1),
            memory_id=f"mem-busy-{index:02d}",
            memory_hash=f"h{index}",
            catalog_snapshot=[],
            state=CategoryJobState.QUEUED,
            created_at=NOW + timedelta(seconds=index),
            next_attempt_at=NOW,
            attempts=0,
        )
        for index in range(16)
    ]
    ready = owned_job(
        id=uuid.UUID(int=17),
        memory_id="mem-ready-17",
        memory_hash="h17",
        catalog_snapshot=[{"name": "support", "description": "Cases"}],
        state=CategoryJobState.QUEUED,
        created_at=NOW + timedelta(seconds=16),
        next_attempt_at=NOW,
        attempts=0,
    )
    session.execute.side_effect = [
        scalar_rows(*busy),
        scalar_rows(ready),
        scalar_result(ready),
    ]
    job_store.try_memory_fence = MagicMock(
        side_effect=[*(nullcontext(None) for _ in busy), nullcontext(session)]
    )

    claimed = job_store.claim("worker-1", NOW, lease_seconds=30)

    assert claimed is not None
    assert claimed.id == ready.id
    assert job_store.try_memory_fence.call_count == 17
    second_page_sql = str(
        session.execute.call_args_list[1].args[0].compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "category_jobs.created_at >" in second_page_sql
    assert "category_jobs.id >" in second_page_sql


def test_claim_returns_none_after_keyset_paging_through_all_busy_candidates(
    job_store, session_factory
):
    session = session_factory.return_value
    jobs = [
        owned_job(
            id=uuid.UUID(int=index + 1),
            memory_id=f"mem-busy-{index:02d}",
            memory_hash=f"h{index}",
            catalog_snapshot=[],
            state=CategoryJobState.QUEUED,
            created_at=NOW + timedelta(seconds=index),
            next_attempt_at=NOW,
            attempts=0,
        )
        for index in range(17)
    ]
    session.execute.side_effect = [scalar_rows(*jobs[:16]), scalar_rows(jobs[16])]
    job_store.try_memory_fence = MagicMock(
        side_effect=[nullcontext(None) for _ in jobs]
    )

    assert job_store.claim("worker-1", NOW, lease_seconds=30) is None

    assert job_store.try_memory_fence.call_count == 17
    assert session.execute.call_count == 2


def test_try_fence_claim_releases_connections_under_pool_pressure():
    @compiles(JSONB, "sqlite")
    def compile_jsonb_as_json(_type, _compiler, **_kwargs):
        return "JSON"

    engine = create_engine(
        "sqlite://",
        poolclass=QueuePool,
        pool_size=1,
        max_overflow=0,
        pool_timeout=0.1,
    )

    tried = []
    unlocked = []

    @event.listens_for(engine, "connect")
    def register_advisory_functions(connection, _record):
        connection.create_function(
            "hashtextextended",
            2,
            lambda key, _seed: 1 if key.endswith("mem-busy") else 2,
        )

        def try_lock(key):
            tried.append(key)
            return key != 1

        def unlock(key):
            unlocked.append(key)
            return 1

        connection.create_function("pg_try_advisory_lock", 1, try_lock)
        connection.create_function("pg_advisory_unlock", 1, unlock)

    Base.metadata.create_all(engine, tables=[CategoryJob.__table__])
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    session = sessions()
    busy = owned_job(
        memory_id="mem-busy",
        memory_hash="h1",
        catalog_snapshot=[{"name": "billing", "description": "Invoices"}],
        state=CategoryJobState.QUEUED,
        created_at=datetime(2026, 1, 1),
        next_attempt_at=datetime(2026, 1, 1),
    )
    ready = owned_job(
        memory_id="mem-ready",
        memory_hash="h2",
        catalog_snapshot=[{"name": "support", "description": "Cases"}],
        state=CategoryJobState.QUEUED,
        created_at=datetime(2026, 1, 2),
        next_attempt_at=datetime(2026, 1, 1),
    )
    session.add_all([busy, ready])
    session.commit()
    session.close()

    claimed = CategoryJobStore(sessions).claim(
        "worker-1", datetime(2026, 1, 3), lease_seconds=30
    )

    assert claimed is not None
    assert claimed.id == ready.id
    assert tried == [1, 2]
    assert unlocked == [2]
    assert engine.pool.checkedout() == 0
    engine.dispose()


def test_claim_limits_candidate_batch_before_trying_fences(job_store, session_factory):
    session = session_factory.return_value
    first = owned_job(
        id=uuid.uuid4(),
        memory_id="mem-1",
        catalog_snapshot=[],
        state=CategoryJobState.QUEUED,
        created_at=NOW,
        next_attempt_at=NOW,
        attempts=0,
    )
    second = owned_job(
        id=uuid.uuid4(),
        memory_id="mem-2",
        catalog_snapshot=[],
        state=CategoryJobState.QUEUED,
        created_at=NOW + timedelta(seconds=1),
        next_attempt_at=NOW,
        attempts=0,
    )

    session.execute.side_effect = [scalar_rows(first, second), scalar_result(first)]
    job_store.try_memory_fence = MagicMock(return_value=nullcontext(session))

    claimed = job_store.claim("worker-1", NOW, lease_seconds=30)

    assert claimed is not None
    assert claimed.id == first.id
    candidate_sql = str(
        session.execute.call_args_list[0].args[0].compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "LIMIT 16" in candidate_sql


def test_claim_quarantines_corrupt_catalog_and_continues_to_valid_candidate(
    job_store, session_factory
):
    session = session_factory.return_value
    corrupt = owned_job(
        id=uuid.uuid4(),
        memory_id="mem-corrupt",
        memory_hash="h1",
        catalog_snapshot=[{"name": "INVALID", "description": "bad"}],
        state=CategoryJobState.QUEUED,
        created_at=NOW,
        next_attempt_at=NOW,
        attempts=0,
    )
    valid = owned_job(
        id=uuid.uuid4(),
        memory_id="mem-valid",
        memory_hash="h2",
        catalog_snapshot=[{"name": "support", "description": "Cases"}],
        state=CategoryJobState.QUEUED,
        created_at=NOW + timedelta(seconds=1),
        next_attempt_at=NOW,
        attempts=0,
    )
    session.execute.side_effect = [
        scalar_rows(corrupt, valid),
        scalar_result(corrupt),
        scalar_result(valid),
    ]
    job_store.try_memory_fence = MagicMock(
        side_effect=[nullcontext(session), nullcontext(session)]
    )

    claimed = job_store.claim("worker-1", NOW, lease_seconds=30)

    assert claimed is not None
    assert claimed.id == valid.id
    assert corrupt.state == CategoryJobState.RETRYING
    assert corrupt.error_code == "_terminalizing_0_category_error"
    assert corrupt.error_message == "Category classification failed"
    assert corrupt.attempts == 0


def test_claim_terminalizing_job_bypasses_malformed_catalog_validation(
    job_store, session_factory
):
    session = session_factory.return_value
    terminalizing = owned_job(
        id=uuid.uuid4(),
        memory_id="mem-terminalizing",
        memory_hash="h1",
        catalog_snapshot=[{"not": "a category"}],
        state=CategoryJobState.RETRYING,
        created_at=NOW,
        next_attempt_at=NOW,
        attempts=3,
        error_code="_terminalizing_2_invalid_json",
        error_message="Invalid category response",
    )
    session.execute.side_effect = [scalar_rows(terminalizing), scalar_result(terminalizing)]
    job_store.try_memory_fence = MagicMock(return_value=nullcontext(session))

    claimed = job_store.claim("worker-1", NOW, lease_seconds=30)

    assert claimed is not None
    assert claimed.id == terminalizing.id
    assert claimed.catalog == ()
    assert claimed.terminalizing is True
    assert claimed.terminal_error_code == "invalid_json"
    assert claimed.attempts == 3


@pytest.mark.parametrize(
    "malformed_count",
    ["²", "9" * 40],
    ids=["unicode-superscript", "oversized-ascii"],
)
def test_claim_quarantines_malformed_terminal_retry_count_and_continues(
    job_store, session_factory, malformed_count
):
    session = session_factory.return_value
    corrupt = owned_job(
        id=uuid.uuid4(),
        memory_id="mem-corrupt-marker",
        memory_hash="h1",
        catalog_snapshot=[],
        state=CategoryJobState.RETRYING,
        created_at=NOW,
        next_attempt_at=NOW,
        attempts=3,
        error_code=f"_terminalizing_{malformed_count}_invalid_json",
        error_message="Invalid category response",
    )
    valid = owned_job(
        id=uuid.uuid4(),
        memory_id="mem-valid",
        memory_hash="h2",
        catalog_snapshot=[{"name": "support", "description": "Cases"}],
        state=CategoryJobState.QUEUED,
        created_at=NOW + timedelta(seconds=1),
        next_attempt_at=NOW,
        attempts=0,
    )
    session.execute.side_effect = [
        scalar_rows(corrupt, valid),
        scalar_result(corrupt),
        scalar_result(valid),
    ]
    job_store.try_memory_fence = MagicMock(
        side_effect=[nullcontext(session), nullcontext(session)]
    )

    claimed = job_store.claim("worker-1", NOW, lease_seconds=30)

    assert claimed is not None
    assert claimed.id == valid.id
    assert corrupt.state == CategoryJobState.RETRYING
    assert corrupt.error_code == "_terminalizing_0_category_error"
    assert corrupt.error_message == "Category classification failed"
    assert corrupt.attempts == 3


def test_complete_only_finishes_the_workers_claim(job_store, session_factory):
    job = owned_job(
        id=uuid.uuid4(),
        memory_id="mem-1",
        catalog_snapshot=[],
        state=CategoryJobState.PROCESSING,
        worker_id="worker-1",
        lease_expires_at=NOW + timedelta(seconds=30),
    )
    session_factory.return_value.get.side_effect = lambda *_args: job
    session_factory.return_value.execute.return_value = scalar_result(job)

    assert job_store.complete(job.id, "worker-1", owner_id=OWNER_ID, now=NOW) is True
    assert job.state == CategoryJobState.COMPLETED
    assert job.worker_id is None
    assert job.lease_expires_at is None
    assert job_store.complete(job.id, "worker-2", owner_id=OWNER_ID) is False


def test_fenced_final_write_can_complete_its_attempt_after_lease_time(job_store, session_factory):
    job = owned_job(
        id=uuid.uuid4(),
        memory_id="mem-1",
        catalog_snapshot=[],
        state=CategoryJobState.PROCESSING,
        worker_id="worker-1",
        lease_expires_at=NOW - timedelta(seconds=1),
    )
    session_factory.return_value.execute.return_value = scalar_result(job)

    assert job_store.complete(
        job.id,
        "worker-1",
        owner_id=OWNER_ID,
        now=NOW,
        lease_fenced=True,
    ) is True
    assert job.state == CategoryJobState.COMPLETED


def test_reschedule_retries_with_bounded_backoff_and_safe_error_fields(job_store, session_factory):
    job = owned_job(
        id=uuid.uuid4(),
        memory_id="mem-1",
        catalog_snapshot=[],
        state=CategoryJobState.PROCESSING,
        worker_id="worker-1",
        attempts=2,
        lease_expires_at=NOW + timedelta(seconds=30),
    )
    session_factory.return_value.get.side_effect = lambda *_args: job
    session_factory.return_value.execute.return_value = scalar_result(job)

    state = job_store.reschedule_or_fail(
        job.id,
        "worker-1",
        owner_id=OWNER_ID,
        now=NOW,
        error_code="provider error!",
        error_message="Category provider request failed\x00",
    )

    assert state == CategoryJobState.RETRYING
    assert job.next_attempt_at == NOW + timedelta(seconds=4)
    assert job.error_code == "provider_error"
    assert job.error_message == "Category provider request failed"
    assert job.worker_id is None
    assert job.lease_expires_at is None


def test_reschedule_never_persists_raw_provider_text_or_credentials(job_store, session_factory):
    job = owned_job(
        id=uuid.uuid4(),
        memory_id="mem-secret",
        catalog_snapshot=[],
        state=CategoryJobState.PROCESSING,
        worker_id="worker-1",
        attempts=1,
        lease_expires_at=NOW + timedelta(seconds=30),
    )
    session = session_factory.return_value
    session.get.side_effect = lambda *_args: job
    session.execute.return_value = scalar_result(job)
    raw = "Memory says swordfish; provider returned sk-secret-token in invalid response"

    job_store.reschedule_or_fail(
        job.id,
        "worker-1",
        owner_id=OWNER_ID,
        now=NOW,
        error_code="arbitrary provider exception",
        error_message=raw,
    )

    assert job.error_code == "category_error"
    assert job.error_message == "Category classification failed"
    assert "swordfish" not in job.error_message
    assert "sk-secret-token" not in job.error_message


def test_reschedule_keeps_terminal_failure_leased_until_payload_write_succeeds(job_store, session_factory):
    job = owned_job(
        id=uuid.uuid4(),
        memory_id="mem-1",
        catalog_snapshot=[],
        state=CategoryJobState.PROCESSING,
        worker_id="worker-1",
        attempts=3,
        lease_expires_at=NOW + timedelta(seconds=30),
    )
    session_factory.return_value.get.side_effect = lambda *_args: job
    session_factory.return_value.execute.return_value = scalar_result(job)

    state = job_store.reschedule_or_fail(
        job.id,
        "worker-1",
        owner_id=OWNER_ID,
        now=NOW,
        error_code="invalid_json",
        error_message="Invalid category response",
    )

    assert state == CategoryJobState.PROCESSING
    assert job.completed_at is None
    assert job.worker_id == "worker-1"
    assert job.lease_expires_at == NOW + timedelta(seconds=30)
    assert job.error_code == "_terminalizing_0_invalid_json"


def test_terminalizing_recovery_is_durable_and_does_not_increment_classifier_attempts(
    job_store, session_factory
):
    job = owned_job(
        id=uuid.uuid4(),
        memory_id="mem-1",
        memory_hash="h1",
        catalog_snapshot=[{"name": "billing", "description": "Invoices"}],
        state=CategoryJobState.PROCESSING,
        worker_id="dead-worker",
        attempts=3,
        error_code="_terminalizing_invalid_json",
        error_message="Invalid category response",
        created_at=NOW - timedelta(minutes=1),
        lease_expires_at=NOW,
    )
    session = session_factory.return_value
    session.execute.side_effect = [scalar_rows(job), scalar_result(job)]
    job_store.try_memory_fence = MagicMock(return_value=nullcontext(session))

    reclaimed = job_store.claim("restart-worker", NOW, lease_seconds=30)

    assert reclaimed is not None
    assert reclaimed.attempts == 3
    assert reclaimed.terminalizing is True
    assert reclaimed.terminal_error_code == "invalid_json"
    assert reclaimed.terminal_error_message == "Invalid category response"
    assert job.worker_id == "restart-worker"


def test_terminal_payload_retry_uses_bounded_backoff_without_losing_internal_marker(
    job_store, session_factory
):
    job = owned_job(
        id=uuid.uuid4(),
        memory_id="mem-1",
        catalog_snapshot=[],
        state=CategoryJobState.PROCESSING,
        worker_id="worker-1",
        attempts=9,
        error_code="_terminalizing_9_provider_error",
        error_message="Category provider request failed",
        lease_expires_at=NOW + timedelta(seconds=30),
    )
    session_factory.return_value.execute.return_value = scalar_result(job)

    assert job_store.reschedule_terminalization(
        job.id,
        "worker-1",
        owner_id=OWNER_ID,
        now=NOW,
        max_backoff_seconds=60,
    ) is True

    assert job.state == CategoryJobState.RETRYING
    assert job.next_attempt_at == NOW + timedelta(seconds=60)
    assert job.error_code == "_terminalizing_10_provider_error"
    assert job.attempts == 9
    assert job.worker_id is None
    assert job.lease_expires_at is None


def test_terminal_retry_count_survives_restart_without_changing_classifier_attempts(
    job_store, session_factory
):
    job = owned_job(
        id=uuid.uuid4(),
        memory_id="mem-1",
        memory_hash="h1",
        catalog_snapshot=[],
        state=CategoryJobState.RETRYING,
        attempts=3,
        error_code="_terminalizing_1_provider_error",
        error_message="Category provider request failed",
        created_at=NOW - timedelta(minutes=1),
        next_attempt_at=NOW,
    )
    session = session_factory.return_value
    session.execute.side_effect = [scalar_rows(job), scalar_result(job)]
    job_store.try_memory_fence = MagicMock(return_value=nullcontext(session))

    reclaimed = job_store.claim("restart-worker", NOW, lease_seconds=30)

    assert reclaimed is not None
    assert reclaimed.terminalizing is True
    assert reclaimed.terminal_error_code == "provider_error"
    assert reclaimed.attempts == 3
    assert job.attempts == 3

    session.execute.side_effect = None
    session.execute.return_value = scalar_result(job)
    assert job_store.reschedule_terminalization(
        job.id,
        "restart-worker",
        owner_id=OWNER_ID,
        now=NOW,
        max_backoff_seconds=60,
    ) is True
    assert job.attempts == 3
    assert job.error_code == "_terminalizing_2_provider_error"
    assert job.next_attempt_at == NOW + timedelta(seconds=4)


def test_fenced_terminal_payload_failure_can_back_off_after_lease_time(
    job_store, session_factory
):
    job = owned_job(
        id=uuid.uuid4(),
        memory_id="mem-1",
        catalog_snapshot=[],
        state=CategoryJobState.PROCESSING,
        worker_id="worker-1",
        attempts=3,
        error_code="_terminalizing_provider_error",
        error_message="Category provider request failed",
        lease_expires_at=NOW - timedelta(seconds=1),
    )
    session_factory.return_value.execute.return_value = scalar_result(job)

    assert job_store.reschedule_terminalization(
        job.id,
        "worker-1",
        owner_id=OWNER_ID,
        now=NOW,
        max_backoff_seconds=60,
        lease_fenced=True,
    ) is True

    assert job.state == CategoryJobState.RETRYING


def test_fail_only_terminalizes_the_workers_live_claim_after_payload_write(job_store, session_factory):
    job = owned_job(
        id=uuid.uuid4(),
        memory_id="mem-1",
        catalog_snapshot=[],
        state=CategoryJobState.PROCESSING,
        worker_id="worker-1",
        attempts=3,
        lease_expires_at=NOW + timedelta(seconds=30),
    )
    session_factory.return_value.execute.return_value = scalar_result(job)

    assert job_store.fail(
        job.id,
        "worker-1",
        owner_id=OWNER_ID,
        now=NOW,
        error_code="invalid_json",
        error_message="Invalid category response",
    ) is True

    assert job.state == CategoryJobState.FAILED
    assert job.completed_at == NOW
    assert job.next_attempt_at is None
    assert job.worker_id is None
    assert job.lease_expires_at is None


def test_expired_terminal_payload_writer_is_reclaimed_without_creating_another_active_job(job_store, session_factory):
    job = owned_job(
        id=uuid.uuid4(),
        memory_id="mem-1",
        memory_hash="h1",
        catalog_snapshot=[{"name": "billing", "description": "Invoices"}],
        state=CategoryJobState.PROCESSING,
        worker_id="dead-worker",
        attempts=3,
        error_code="_terminalizing_invalid_json",
        error_message="Invalid category response",
        created_at=NOW - timedelta(minutes=1),
        lease_expires_at=NOW,
    )
    session = session_factory.return_value
    session.execute.side_effect = [scalar_rows(job), scalar_result(job)]
    job_store.try_memory_fence = MagicMock(return_value=nullcontext(session))

    reclaimed = job_store.claim("restart-worker", NOW, lease_seconds=30)

    assert reclaimed is not None
    assert reclaimed.id == job.id
    assert reclaimed.attempts == 3
    assert reclaimed.terminalizing is True
    assert job.worker_id == "restart-worker"


def test_cancel_active_and_cancel_all_active_only_cancel_active_states(job_store, session_factory):
    session = session_factory.return_value
    active = owned_job(
        id=uuid.uuid4(), memory_id="mem-1", catalog_snapshot=[], state=CategoryJobState.PROCESSING, worker_id="worker-1"
    )
    terminal = owned_job(id=uuid.uuid4(), memory_id="mem-1", catalog_snapshot=[], state=CategoryJobState.COMPLETED)
    session.execute.side_effect = [
        MagicMock(scalars=lambda: MagicMock(all=lambda: [active])),
        MagicMock(scalars=lambda: MagicMock(all=lambda: [active])),
    ]

    assert job_store.cancel_active("mem-1", OWNER_ID) == 1
    assert active.state == CategoryJobState.CANCELLED
    assert terminal.state == CategoryJobState.COMPLETED
    active.state = CategoryJobState.RETRYING
    assert job_store.cancel_all_active(OWNER_ID) == 1
    assert active.state == CategoryJobState.CANCELLED


def test_cancel_active_rejects_a_foreign_owner_with_the_same_memory_id():
    """Single-memory cleanup must not mutate a foreign owner's durable work."""

    @compiles(JSONB, "sqlite")
    def compile_jsonb_as_json(_type, _compiler, **_kwargs):
        return "JSON"

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[CategoryJob.__table__])
    foreign_owner = uuid.UUID("00000000-0000-0000-0000-000000000002")
    sessions = sessionmaker(bind=engine)
    with sessions() as session:
        foreign = owned_job(
            id=uuid.uuid4(),
            memory_id="shared-memory-id",
            owner_id=foreign_owner,
            catalog_snapshot=[],
            state=CategoryJobState.QUEUED,
        )
        session.add(foreign)
        session.commit()
        foreign_id = foreign.id

    assert CategoryJobStore(sessions).cancel_active("shared-memory-id", OWNER_ID) == 0

    with sessions() as session:
        assert session.get(CategoryJob, foreign_id).state == CategoryJobState.QUEUED
    engine.dispose()


def test_cancel_scopes_the_reason_code_to_the_workers_claim(job_store, session_factory):
    job = owned_job(
        id=uuid.uuid4(),
        memory_id="mem-1",
        catalog_snapshot=[],
        state=CategoryJobState.PROCESSING,
        worker_id="worker-1",
        lease_expires_at=NOW + timedelta(seconds=30),
    )
    session_factory.return_value.get.side_effect = lambda *_args: job
    session_factory.return_value.execute.return_value = scalar_result(job)

    assert job_store.cancel(
        job.id, "worker-1", "memory deleted!", owner_id=OWNER_ID, now=NOW
    ) is True
    assert job.state == CategoryJobState.CANCELLED
    assert job.error_code == "memory_deleted"
    assert job_store.cancel(job.id, "worker-2", "ignored", owner_id=OWNER_ID) is False


def test_renew_locks_and_extends_only_the_current_workers_unexpired_lease(job_store, session_factory):
    """A late worker must renew under lock before it can write a payload after a slow model call."""
    job = owned_job(
        id=uuid.uuid4(),
        memory_id="mem-1",
        catalog_snapshot=[],
        state=CategoryJobState.PROCESSING,
        worker_id="worker-1",
        attempts=1,
        lease_expires_at=NOW + timedelta(seconds=30),
    )
    session = session_factory.return_value
    session.execute.return_value = scalar_result(job)

    assert job_store.renew(
        job.id,
        "worker-1",
        owner_id=OWNER_ID,
        now=NOW + timedelta(seconds=5),
        lease_seconds=60,
    ) is True

    statement = session.execute.call_args.args[0]
    assert "FOR UPDATE" in str(statement.compile(dialect=postgresql.dialect()))
    assert job.lease_expires_at == NOW + timedelta(seconds=65)
    session.commit.assert_called_once_with()


@pytest.mark.parametrize(
    ("worker_id", "lease_expires_at"),
    [("other-worker", NOW + timedelta(seconds=30)), ("worker-1", NOW)],
)
def test_renew_rejects_stale_or_expired_claims(job_store, session_factory, worker_id, lease_expires_at):
    """An expired or stolen lease may never be extended by the old worker."""
    job = owned_job(
        id=uuid.uuid4(),
        memory_id="mem-1",
        catalog_snapshot=[],
        state=CategoryJobState.PROCESSING,
        worker_id=worker_id,
        attempts=1,
        lease_expires_at=lease_expires_at,
    )
    session = session_factory.return_value
    session.execute.return_value = scalar_result(job)

    assert job_store.renew(
        job.id, "worker-1", owner_id=OWNER_ID, now=NOW, lease_seconds=60
    ) is False

    assert job.lease_expires_at == lease_expires_at
    session.commit.assert_not_called()


@pytest.mark.parametrize("operation", ["complete", "reschedule", "cancel"])
def test_claim_mutations_lock_the_row_and_reject_an_expired_lease(operation, job_store, session_factory):
    job = owned_job(
        id=uuid.uuid4(),
        memory_id="mem-1",
        catalog_snapshot=[],
        state=CategoryJobState.PROCESSING,
        worker_id="worker-1",
        attempts=1,
        lease_expires_at=NOW,
    )
    session = session_factory.return_value
    session.get.side_effect = lambda *_args: job
    session.execute.return_value = scalar_result(job)

    if operation == "complete":
        result = job_store.complete(job.id, "worker-1", owner_id=OWNER_ID, now=NOW)
    elif operation == "reschedule":
        result = job_store.reschedule_or_fail(
            job.id,
            "worker-1",
            owner_id=OWNER_ID,
            now=NOW,
            error_code="provider_error",
            error_message="Category provider request failed",
        )
    else:
        result = job_store.cancel(
            job.id, "worker-1", "memory_deleted", owner_id=OWNER_ID, now=NOW
        )

    statement = session.execute.call_args.args[0]
    assert "FOR UPDATE" in str(statement.compile(dialect=postgresql.dialect()))
    assert result in (False, None)
    assert job.state == CategoryJobState.PROCESSING
    session.commit.assert_not_called()


@pytest.mark.parametrize("operation", ["complete", "reschedule", "cancel"])
def test_claim_mutations_reject_a_stale_worker(operation, job_store, session_factory):
    job = owned_job(
        id=uuid.uuid4(),
        memory_id="mem-1",
        catalog_snapshot=[],
        state=CategoryJobState.PROCESSING,
        worker_id="new-worker",
        attempts=1,
        lease_expires_at=NOW + timedelta(seconds=30),
    )
    session = session_factory.return_value
    session.get.side_effect = lambda *_args: job
    session.execute.return_value = scalar_result(job)

    if operation == "complete":
        result = job_store.complete(job.id, "stale-worker", owner_id=OWNER_ID, now=NOW)
    elif operation == "reschedule":
        result = job_store.reschedule_or_fail(
            job.id,
            "stale-worker",
            owner_id=OWNER_ID,
            now=NOW,
            error_code="provider_error",
            error_message="Category provider request failed",
        )
    else:
        result = job_store.cancel(
            job.id, "stale-worker", "memory_deleted", owner_id=OWNER_ID, now=NOW
        )

    assert result in (False, None)
    assert job.worker_id == "new-worker"
    assert job.state == CategoryJobState.PROCESSING
    session.commit.assert_not_called()


def test_list_jobs_returns_newest_matching_states(job_store, session_factory):
    failed = owned_job(id=uuid.uuid4(), memory_id="mem-1", catalog_snapshot=[], state=CategoryJobState.FAILED)
    session_factory.return_value.execute.return_value = MagicMock(scalars=lambda: MagicMock(all=lambda: [failed]))

    jobs = job_store.list_jobs(owner_id=OWNER_ID, states=(CategoryJobState.FAILED,), limit=5)

    assert jobs == [failed]


def test_owner_scoped_job_listing_and_reset_purge_leave_another_owners_jobs_untouched():
    """Removing either owner predicate exposes or deletes another account's category work."""

    @compiles(JSONB, "sqlite")
    def compile_jsonb_as_json(_type, _compiler, **_kwargs):
        return "JSON"

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[CategoryJob.__table__])
    owner_b = uuid.UUID("00000000-0000-0000-0000-000000000002")
    with sessionmaker(bind=engine)() as session:
        session.add_all(
            [
                owned_job(
                    id=uuid.uuid4(),
                    memory_id=f"memory-a-{state.value}",
                    owner_id=OWNER_ID,
                    catalog_snapshot=[],
                    state=state,
                )
                for state in CategoryJobState
            ]
            + [
                owned_job(
                    id=uuid.uuid4(),
                    memory_id="memory-a-preparing",
                    owner_id=OWNER_ID,
                    catalog_snapshot=[],
                    state="preparing",
                ),
                owned_job(id=uuid.uuid4(), memory_id="memory-b-queued", owner_id=owner_b, catalog_snapshot=[]),
                owned_job(
                    id=uuid.uuid4(),
                    memory_id="memory-b-completed",
                    owner_id=owner_b,
                    catalog_snapshot=[],
                    state=CategoryJobState.COMPLETED,
                ),
            ]
        )
        session.commit()

    job_store = CategoryJobStore(sessionmaker(bind=engine))

    assert {job.owner_id for job in job_store.list_jobs(owner_id=OWNER_ID)} == {OWNER_ID}
    assert job_store.purge_owner(OWNER_ID) == len(CategoryJobState) + 1

    with sessionmaker(bind=engine)() as session:
        rows = session.query(CategoryJob).all()
    assert {job.memory_id for job in rows} == {"memory-b-queued", "memory-b-completed"}
    assert {job.owner_id for job in rows} == {owner_b}
    engine.dispose()
