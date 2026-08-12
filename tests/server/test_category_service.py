"""Contract tests for category lifecycle and explicit reclassification."""

import threading
import uuid
from contextlib import contextmanager, nullcontext
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from category_models import CategoryDefinition, CategoryJobState, EffectiveCatalog
from category_store import EnqueueResult, MemoryCategoryStore, MemorySnapshot, PreparedCategoryJob
from category_service import CategoryService, CategoryUpdateOutcome


PROJECT = (
    CategoryDefinition(name="billing", description="Invoices and payments."),
    CategoryDefinition(name="support", description="Customer support cases."),
)
REQUEST = (CategoryDefinition(name="travel", description="Travel plans."),)
OWNER_ID = "00000000-0000-0000-0000-000000000001"
OWNER_UUID = uuid.UUID(OWNER_ID)
OWNER_B_ID = "00000000-0000-0000-0000-000000000002"


def snapshot(
    memory_id,
    *,
    user_id=OWNER_ID,
    text="Memory text",
    memory_hash="h1",
    status="unclassified",
    categories=None,
    generation=None,
    origin=None,
):
    """Build an immutable shape returned by the real memory category store."""
    values = dict(
        memory_id=memory_id,
        user_id=user_id,
        text=text,
        memory_hash=memory_hash,
        categories=categories,
        category_status=status,
        payload={} if origin is None else {"_category_origin": origin},
    )
    if generation is not None:
        values["category_generation"] = generation
    if origin is not None:
        values["category_origin"] = origin
    return MemorySnapshot(**values)


class AtomicVectorStore:
    """Small real CAS payload store used by interleaving regressions."""

    def __init__(self, payload):
        self.payload = deepcopy(payload)
        self.payload.setdefault("user_id", OWNER_ID)
        self._lock = threading.Lock()

    def get(self, _memory_id):
        with self._lock:
            return SimpleNamespace(id="m1", payload=deepcopy(self.payload))

    def _patch_payload(self, _memory_id, fields, *, expected=None):
        with self._lock:
            if expected and any(self.payload.get(key) != value for key, value in expected.items()):
                return None
            self.payload.update(deepcopy(fields))
            return SimpleNamespace(id="m1", payload=deepcopy(self.payload))


class FencedJobStore:
    """In-memory job state with the same observable fence/install contract as PostgreSQL."""

    def __init__(self, active=None):
        self._lock = threading.Lock()
        self.active = active
        self.active_hash = "h1"
        self.active_catalog = None
        self.prepared = {}
        self.second_waiting = threading.Event()
        self.second_installed = threading.Event()

    @contextmanager
    def memory_fence(self, _memory_id):
        if threading.current_thread().name == "enqueue-b":
            self.second_waiting.set()
        with self._lock:
            yield

    def active_matches(self, _memory_id, memory_hash, generation, *, owner_id):
        return self.active is not None and str(self.active) == generation and self.active_hash == memory_hash

    def enqueue(self, _memory_id, memory_hash, catalog, *, job_id, **_kwargs):
        # Round-2 behavior: installing here lets a later caller invert marker/job order.
        self.active = job_id
        self.active_hash = memory_hash
        self.active_catalog = tuple(catalog)
        if threading.current_thread().name == "enqueue-b":
            self.second_installed.set()
        return EnqueueResult(job_id=job_id, created=True)

    def prepare(self, _memory_id, memory_hash, catalog, *, job_id, owner_id):
        self.prepared[job_id] = (owner_id, memory_hash, tuple(catalog))
        return EnqueueResult(job_id=job_id, created=True)

    def install_prepared(self, job_id, owner_id):
        prepared_owner, memory_hash, catalog = self.prepared.pop(job_id)
        assert prepared_owner == owner_id
        self.active = job_id
        self.active_hash = memory_hash
        self.active_catalog = catalog
        return True

    def cancel_prepared(self, job_id, _owner_id):
        self.prepared.pop(job_id, None)
        return True

    def preparation_is_latest(self, _job_id, _memory_id, _owner_id):
        return True


@pytest.fixture
def catalog_store():
    store = MagicMock(name="catalog_store")
    store.get_saved.return_value = ()
    return store


@pytest.fixture
def job_store():
    store = MagicMock(name="job_store")
    reservation = EnqueueResult(job_id=uuid.uuid4(), created=True)
    store.enqueue.return_value = reservation
    store.prepare.return_value = reservation
    store.install_prepared.return_value = True
    store.memory_fence.return_value = nullcontext()
    store.active_matches.return_value = False
    store.prepared_matches.return_value = False
    store.list_prepared.return_value = ()
    store.preparation_is_latest.return_value = True
    return store


@pytest.fixture
def memory_store():
    store = MagicMock(name="memory_store")
    store.mark_pending.return_value = snapshot("m1")
    store.get.return_value = snapshot("m1")
    store.iter_snapshots.return_value = []
    store.iter_all_snapshots = store.iter_snapshots
    store.category_counts.return_value = {}
    return store


@pytest.fixture
def classifier():
    return MagicMock(name="classifier")


@pytest.fixture
def service(catalog_store, job_store, memory_store, classifier):
    return CategoryService(catalog_store, job_store, memory_store, classifier)


def test_resolve_catalog_uses_request_then_the_authenticated_owners_saved_catalog_then_defaults(service, catalog_store):
    """A wrong precedence branch would classify a request with the wrong catalog."""
    catalog_store.get_saved.return_value = PROJECT

    assert service.resolve_catalog(OWNER_ID, REQUEST) == EffectiveCatalog(definitions=REQUEST, source="request")
    assert service.resolve_catalog(OWNER_ID, None) == EffectiveCatalog(definitions=PROJECT, source="project")
    catalog_store.get_saved.assert_called_with(OWNER_ID)

    catalog_store.get_saved.return_value = ()
    assert service.resolve_catalog(OWNER_ID, None).source == "defaults"


def test_reclassification_uses_the_requested_owners_saved_catalog(service, catalog_store, memory_store, classifier):
    """Resolving another account's catalog would queue valid jobs with the wrong labels."""
    catalog_store.get_saved.side_effect = lambda owner_id, *, session=None: PROJECT if owner_id == OWNER_ID else REQUEST
    memory_store.iter_snapshots.return_value = [snapshot("m1", user_id=OWNER_B_ID)]
    classifier.estimate_tokens.return_value = (3, 1)

    preview = service.preview_reclassification(scope="all", owner_id=OWNER_B_ID)

    assert preview.estimated_input_tokens == 3
    assert classifier.estimate_tokens.call_args.args[1] == REQUEST


def test_resolve_catalog_rejects_an_explicit_empty_request_catalog(service, catalog_store):
    """Treating an explicit empty override as absent would silently classify with the wrong catalog."""
    catalog_store.get_saved.return_value = PROJECT

    with pytest.raises(ValueError, match="must not be empty"):
        service.resolve_catalog(OWNER_ID, ())


def test_catalog_view_keeps_retired_labels_in_counts(service, catalog_store, memory_store):
    """Filtering counts to active labels would hide still-categorized retired memories."""
    catalog_store.get_saved.return_value = PROJECT
    memory_store.category_counts.return_value = {"billing": 3, "retired": 2}

    view = service.get_catalog_view(OWNER_ID)

    assert view.saved == PROJECT
    assert view.active == PROJECT
    assert view.source == "project"
    assert view.counts == {"billing": 3, "retired": 2}


def test_create_category_appends_to_the_saved_catalog_without_enqueueing(service, catalog_store, job_store):
    """Catalog CRUD must only affect later ingestion, never historical memories."""
    created = CategoryDefinition(name="legal", description="Contracts and legal terms.")
    catalog_store.create.return_value = (*PROJECT, created)

    view = service.create_category(created, OWNER_ID)

    assert view.saved == (*PROJECT, created)
    catalog_store.create.assert_called_once_with(OWNER_ID, created)
    job_store.enqueue.assert_not_called()


def test_replace_catalog_resets_to_defaults_without_enqueueing_historical_memories(service, job_store):
    """A reset must not silently begin an expensive historical backfill."""
    view = service.replace_catalog((), OWNER_ID)

    assert view.saved == ()
    assert view.source == "defaults"
    job_store.enqueue.assert_not_called()


def test_update_category_replaces_the_named_definition_and_allows_explicit_rename(service, catalog_store, job_store):
    """Updating the wrong catalog row would change a different classifier label."""
    replacement = CategoryDefinition(name="invoices", description="Invoices, receipts, and payments.")
    catalog_store.update.return_value = (replacement, PROJECT[1])

    view = service.update_category(
        "billing", OWNER_ID, new_name=replacement.name, description=replacement.description
    )

    assert view.saved == (replacement, PROJECT[1])
    catalog_store.update.assert_called_once_with(
        OWNER_ID, "billing", new_name=replacement.name, description=replacement.description
    )
    job_store.enqueue.assert_not_called()


def test_update_category_rejects_an_unknown_name(service, catalog_store):
    """Silently accepting an absent name would make catalog edits appear to work when they did not."""
    catalog_store.update.side_effect = KeyError("missing")
    with pytest.raises(KeyError, match="missing"):
        service.update_category("missing", OWNER_ID, description="Changed")


def test_delete_category_removes_only_the_named_definition(service, catalog_store, job_store):
    """Deleting an adjacent category would alter future classification allowlists."""
    catalog_store.delete.return_value = (PROJECT[1],)

    view = service.delete_category("billing", OWNER_ID)

    assert view.saved == (PROJECT[1],)
    catalog_store.delete.assert_called_once_with(OWNER_ID, "billing")
    job_store.enqueue.assert_not_called()


def test_delete_category_rejects_an_unknown_name(service, catalog_store):
    """A missing delete target must not masquerade as a successful catalog change."""
    catalog_store.delete.side_effect = KeyError("missing")
    with pytest.raises(KeyError, match="missing"):
        service.delete_category("missing", OWNER_ID)


def test_list_jobs_delegates_the_validated_filter_to_the_job_store(service, job_store):
    """Routes must not reach through the service into durable storage internals."""
    expected = [MagicMock(name="failed_job")]
    job_store.list_jobs.return_value = expected

    jobs = service.list_jobs(owner_id=OWNER_ID, states=(CategoryJobState.FAILED,), limit=7)

    assert jobs == expected
    job_store.list_jobs.assert_called_once_with(
        owner_id=uuid.UUID(OWNER_ID), states=(CategoryJobState.FAILED,), limit=7
    )


def test_enqueue_memory_marks_pending_and_snapshots_its_hash(service, job_store, memory_store):
    """Using a stale hash or skipping pending state could let a worker overwrite a newer memory."""
    memory_store.get.return_value = snapshot("m1", memory_hash="current-hash")
    memory_store.mark_pending.return_value = snapshot("m1", memory_hash="current-hash")
    catalog = EffectiveCatalog(definitions=PROJECT, source="project")

    assert service.enqueue_memory("m1", catalog) is True

    prepare = job_store.prepare.call_args
    assert prepare.args == ("m1", "current-hash", PROJECT)
    assert isinstance(prepare.kwargs["job_id"], uuid.UUID)
    memory_store.mark_pending.assert_called_once()
    job_store.install_prepared.assert_called_once_with(
        job_store.prepare.return_value.job_id, OWNER_UUID
    )
    assert memory_store.mark_pending.call_args.args[1] == str(job_store.prepare.return_value.job_id)
    assert memory_store.mark_pending.call_args.kwargs["expected_generation"] is None


def test_enqueue_failure_is_non_blocking_and_observable(service, memory_store, job_store, caplog):
    """A durable-job outage must not undo an already-written memory or leak sensitive failure detail."""
    job_store.prepare.side_effect = RuntimeError("database unavailable: secret raw row")

    assert service.enqueue_memory("m1", EffectiveCatalog(definitions=PROJECT, source="project")) is False

    memory_store.mark_pending.assert_not_called()
    memory_store.write_result.assert_not_called()
    assert "m1" in caplog.text
    assert "enqueue_failed" in caplog.text
    assert "secret raw row" not in caplog.text


def test_replacement_enqueue_failure_terminalizes_only_its_own_pending_generation(
    service, memory_store, job_store
):
    memory_store.get.return_value = snapshot("m1", text="Current", memory_hash="h1")
    memory_store.mark_pending.return_value = snapshot(
        "m1", text="Current", memory_hash="h1", status="pending"
    )
    job_store.install_prepared.side_effect = RuntimeError("database unavailable")

    assert service.enqueue_memory(
        "m1",
        EffectiveCatalog(definitions=PROJECT, source="project"),
        replace_active=True,
        expected_text="Current",
    ) is False

    generation = memory_store.mark_pending.call_args.args[1]
    memory_store.restore.assert_not_called()
    memory_store.write_result.assert_called_once_with(
        "m1", "h1", generation, [], "failed", owner_id=OWNER_UUID
    )


def test_staged_pending_cas_failure_cancels_the_unclaimable_reservation(
    service, memory_store, job_store
):
    reservation = EnqueueResult(job_id=uuid.uuid4(), created=True)
    job_store.prepare.return_value = reservation
    memory_store.mark_pending.return_value = None

    assert service.enqueue_memory(
        "m1", EffectiveCatalog(definitions=PROJECT, source="project"), replace_active=False
    ) is False

    job_store.cancel_prepared.assert_called_once_with(reservation.job_id, OWNER_UUID)


def test_pending_marker_exception_preserves_the_durable_reservation(service, memory_store, job_store):
    reservation = EnqueueResult(job_id=uuid.uuid4(), created=True)
    job_store.prepare.return_value = reservation
    memory_store.mark_pending.side_effect = RuntimeError("vector store unavailable")

    assert service.enqueue_memory(
        "m1", EffectiveCatalog(definitions=PROJECT, source="project"), replace_active=True
    ) is False

    job_store.cancel_prepared.assert_not_called()


def test_origin_tombstone_superseded_by_newer_work_terminalizes_marker_without_loop(
    service, memory_store, job_store
):
    origin = str(uuid.uuid4())
    current = snapshot("m1", origin=origin)
    reservation = EnqueueResult(
        job_id=service._generation_for_origin(origin, "m1"),
        created=True,
    )
    memory_store.get.return_value = current
    job_store.prepare.return_value = reservation
    job_store.preparation_is_latest.return_value = False

    assert service.enqueue_memory(
        "m1",
        EffectiveCatalog(definitions=PROJECT, source="project"),
        replace_active=True,
        expected_origin=origin,
    ) is False

    memory_store.fail_origin.assert_called_once_with(current)
    memory_store.mark_pending.assert_not_called()


def test_origin_tombstone_never_clears_a_newer_payload_generation(service, memory_store, job_store):
    origin = str(uuid.uuid4())
    current = snapshot("m1", origin=origin, status="pending", generation=str(uuid.uuid4()))
    reservation = EnqueueResult(
        job_id=service._generation_for_origin(origin, "m1"),
        created=True,
    )
    memory_store.get.return_value = current
    job_store.prepare.return_value = reservation
    job_store.preparation_is_latest.return_value = False

    assert service.enqueue_memory(
        "m1",
        EffectiveCatalog(definitions=PROJECT, source="project"),
        replace_active=True,
        expected_origin=origin,
    ) is False

    memory_store.clear_origin.assert_called_once_with(current)
    memory_store.fail_origin.assert_not_called()


def test_replace_callers_cannot_invert_payload_generation_and_active_job():
    """Marker A/B followed by DB B/A must be impossible for one memory."""
    vector = AtomicVectorStore(
            {
                "data": "Same",
                "hash": "h1",
                "user_id": OWNER_ID,
                "categories": None,
            "category_status": "pending",
            "_category_generation": "old-job",
        }
    )

    class PausingMemoryStore(MemoryCategoryStore):
        def __init__(self):
            super().__init__(lambda: SimpleNamespace(vector_store=vector))
            self.calls = 0
            self.first_marked = threading.Event()
            self.second_marked = threading.Event()
            self.release_first = threading.Event()

        def mark_pending(self, *args, **kwargs):
            result = super().mark_pending(*args, **kwargs)
            self.calls += 1
            if self.calls == 1:
                self.first_marked.set()
                assert self.release_first.wait(1)
            else:
                self.second_marked.set()
            return result

    memory_store = PausingMemoryStore()
    job_store = FencedJobStore(active="old-job")
    service = CategoryService(MagicMock(get_saved=lambda: ()), job_store, memory_store, MagicMock())
    errors = []

    def enqueue(name, catalog):
        try:
            service.enqueue_memory(
                "m1",
                EffectiveCatalog(definitions=catalog, source="request"),
                replace_active=True,
                expected_text="Same",
            )
        except Exception as error:
            errors.append(error)

    first = threading.Thread(target=enqueue, name="enqueue-a", args=("a", PROJECT))
    second = threading.Thread(target=enqueue, name="enqueue-b", args=("b", REQUEST))
    first.start()
    assert memory_store.first_marked.wait(1)
    second.start()
    if not job_store.second_installed.wait(0.1):
        assert job_store.second_waiting.wait(1)
    memory_store.release_first.set()
    first.join(1)
    second.join(1)

    assert errors == []
    assert not first.is_alive() and not second.is_alive()
    assert vector.payload["_category_generation"] == str(job_store.active)
    assert job_store.active_catalog == REQUEST


def test_nonreplace_stale_vector_cas_never_cancels_the_newer_active_job(service, memory_store, job_store):
    """Historical work must validate the vector snapshot before touching current active work."""
    new_job = uuid.uuid4()
    job_store.active = new_job
    job_store.memory_fence.return_value = nullcontext()
    job_store.prepare.return_value = EnqueueResult(job_id=uuid.uuid4(), created=True)

    def unsafe_round_two_enqueue(*_args, **_kwargs):
        job_store.active = None
        return EnqueueResult(job_id=uuid.uuid4(), created=True)

    job_store.enqueue.side_effect = unsafe_round_two_enqueue
    memory_store.get.return_value = snapshot("m1", memory_hash="stale-hash")
    memory_store.mark_pending.return_value = None

    assert service.enqueue_memory(
        "m1", EffectiveCatalog(definitions=PROJECT, source="project"), replace_active=False
    ) is False
    assert job_store.active == new_job


def test_prepared_catalog_is_durable_before_pending_marker(service, memory_store, job_store):
    """A crash can never expose pending before the immutable request catalog has a durable job row."""
    events = []
    job_id = uuid.uuid4()
    job_store.memory_fence.return_value = nullcontext()
    job_store.prepare.side_effect = lambda *_args, **_kwargs: (
        events.append("prepare") or EnqueueResult(job_id=job_id, created=True)
    )
    job_store.enqueue.side_effect = lambda *_args, **_kwargs: (
        events.append("enqueue") or EnqueueResult(job_id=job_id, created=True)
    )
    memory_store.mark_pending.side_effect = lambda *_args, **_kwargs: (
        events.append("mark") or snapshot("m1", status="pending", generation=str(job_id))
    )
    job_store.install_prepared.side_effect = lambda *_args: events.append("install") or True
    job_store.activate.side_effect = lambda *_args: events.append("activate") or True

    assert service.enqueue_memory(
        "m1", EffectiveCatalog(definitions=REQUEST, source="request"), replace_active=True
    ) is True
    assert events == ["prepare", "mark", "install"]


def test_failed_prepared_install_does_not_restore_categories_for_the_current_text(
    catalog_store, classifier
):
    """A false activation must not leave pending after a prepared job became terminal or disappeared."""
    vector = AtomicVectorStore(
            {
                "data": "Same",
                "hash": "h1",
                "user_id": OWNER_ID,
                "categories": ["billing"],
            "category_status": "completed",
            "_category_generation": None,
        }
    )
    memory_store = MemoryCategoryStore(lambda: SimpleNamespace(vector_store=vector))
    job_store = FencedJobStore()
    job_store.activate = lambda _job_id: False
    job_store.install_prepared = lambda _job_id: False
    service = CategoryService(catalog_store, job_store, memory_store, classifier)

    assert service.enqueue_memory(
        "m1", EffectiveCatalog(definitions=REQUEST, source="request"), replace_active=True
    ) is False
    assert vector.payload["categories"] == []
    assert vector.payload["category_status"] == "failed"
    assert vector.payload["_category_generation"] is None


def test_metadata_update_fence_prevents_wholesale_core_payload_restore_after_worker_completion(
    catalog_store, classifier
):
    """A metadata-only PUT snapshot cannot overwrite a terminal worker patch with stale pending fields."""
    job_id = uuid.uuid4()
    vector = AtomicVectorStore(
        {
            "data": "Same",
            "hash": "h1",
            "categories": None,
            "category_status": "pending",
            "_category_generation": str(job_id),
            "source": "old",
        }
    )
    memory_store = MemoryCategoryStore(lambda: SimpleNamespace(vector_store=vector))
    job_store = FencedJobStore(active=job_id)
    service = CategoryService(catalog_store, job_store, memory_store, classifier)
    core_read = threading.Event()
    allow_core_write = threading.Event()

    def wholesale_core_update():
        stale = deepcopy(vector.payload)
        core_read.set()
        assert allow_core_write.wait(1)
        stale["source"] = "new"
        with vector._lock:
            vector.payload = stale
        return {"id": "m1", "memory": "Same"}

    update_result = []
    update_thread = threading.Thread(
        target=lambda: update_result.append(
            service.run_memory_update("m1", wholesale_core_update, owner_id=OWNER_ID)
        )
    )
    def worker_write():
        assert core_read.wait(1)
        with job_store.memory_fence("m1"):
            assert memory_store.write_result(
                "m1", "h1", str(job_id), ["billing"], "completed", owner_id=uuid.UUID(OWNER_ID)
            )

    worker_thread = threading.Thread(target=worker_write)
    update_thread.start()
    assert core_read.wait(1)
    worker_thread.start()
    allow_core_write.set()
    update_thread.join(1)
    worker_thread.join(1)

    assert update_result == [{"id": "m1", "memory": "Same"}]
    assert vector.payload["source"] == "new"
    assert vector.payload["categories"] == ["billing"]
    assert vector.payload["category_status"] == "completed"


def test_after_add_classifies_only_add_and_update_events_without_mutating_response(
    service, job_store, memory_store
):
    """Classifying delete events or mutating the core add response would violate ingestion compatibility."""
    response = {
        "results": [
            {"id": "a", "event": "ADD", "memory": "A"},
            {"id": "b", "event": "UPDATE", "memory": "B"},
            {"id": "c", "event": "DELETE", "memory": "C"},
        ]
    }

    memory_store.get.side_effect = [
        snapshot("a", text="A"),
        snapshot("a", text="A"),
        snapshot("b", text="B"),
        snapshot("b", text="B"),
    ]

    result = service.after_add(response, EffectiveCatalog(definitions=PROJECT, source="project"))

    assert [call.args[0] for call in job_store.prepare.call_args_list] == ["a", "b"]
    assert result["results"][0]["categories"] is None
    assert result["results"][0]["category_status"] == "pending"
    assert result["results"][2]["category_status"] == "unclassified"
    assert response == {
        "results": [
            {"id": "a", "event": "ADD", "memory": "A"},
            {"id": "b", "event": "UPDATE", "memory": "B"},
            {"id": "c", "event": "DELETE", "memory": "C"},
        ]
    }


def test_delayed_after_add_does_not_replace_a_newer_memory_or_catalog(service, memory_store, job_store):
    memory_store.get.return_value = snapshot("m1", text="New B", memory_hash="hash-b")
    stale_catalog = EffectiveCatalog(definitions=REQUEST, source="request")

    result = service.after_add(
        {"results": [{"id": "m1", "event": "UPDATE", "memory": "Old A"}]},
        stale_catalog,
    )

    assert result["results"][0]["category_status"] == "unclassified"
    memory_store.mark_pending.assert_not_called()
    job_store.enqueue.assert_not_called()


def test_same_text_delayed_after_add_keeps_the_generation_already_bound_to_active_work(
    service, memory_store, job_store
):
    memory_store.get.return_value = snapshot(
        "m1", text="Same", memory_hash="h1", status="pending", generation="current-job"
    )
    job_store.active_matches.return_value = True

    result = service.after_add(
        {"results": [{"id": "m1", "event": "UPDATE", "memory": "Same"}]},
        EffectiveCatalog(definitions=REQUEST, source="request"),
    )

    assert result["results"][0]["category_status"] == "pending"
    memory_store.mark_pending.assert_not_called()
    job_store.enqueue.assert_not_called()


def test_same_text_newer_origin_replaces_older_catalog_binding(service, memory_store, job_store):
    """Equal text/hash does not make two per-call catalogs the same request."""
    old_job = uuid.uuid4()
    new_origin = str(uuid.uuid4())
    memory_store.get.return_value = snapshot(
        "m1",
        text="Same",
        memory_hash="h1",
        status="pending",
        generation=str(old_job),
        origin=new_origin,
    )
    memory_store.mark_pending.return_value = snapshot("m1", text="Same", status="pending")
    job_store.memory_fence.return_value = nullcontext()
    job_store.active_matches.return_value = True
    prepared = EnqueueResult(job_id=uuid.uuid4(), created=True)
    job_store.prepare.return_value = prepared
    job_store.install_prepared.return_value = True

    result = service.after_add(
        {"results": [{"id": "m1", "event": "UPDATE", "memory": "Same"}]},
        EffectiveCatalog(definitions=REQUEST, source="request"),
        origin_token=new_origin,
    )

    assert result["results"][0]["category_status"] == "pending"
    assert job_store.prepare.call_args.args[2] == REQUEST
    assert memory_store.mark_pending.call_args.kwargs["expected_origin"] == new_origin


def test_same_text_delayed_origin_cannot_replace_newer_request_catalog(service, memory_store, job_store):
    old_origin = str(uuid.uuid4())
    memory_store.get.return_value = snapshot(
        "m1", text="Same", memory_hash="h1", status="pending", origin=str(uuid.uuid4())
    )
    job_store.memory_fence.return_value = nullcontext()

    result = service.after_add(
        {"results": [{"id": "m1", "event": "UPDATE", "memory": "Same"}]},
        EffectiveCatalog(definitions=REQUEST, source="request"),
        origin_token=old_origin,
    )

    assert result["results"][0]["category_status"] == "unclassified"
    job_store.prepare.assert_not_called()
    memory_store.mark_pending.assert_not_called()


def test_unchanged_put_repairs_pending_payload_when_active_job_does_not_match_current_generation(
    service, memory_store, job_store
):
    """A->B followed by a pre-read-A PUT restoring A must replace B's pending job."""
    memory_store.get.return_value = snapshot(
        "m1", text="A", memory_hash="hash-a", status="pending", generation="job-for-b"
    )
    memory_store.mark_pending.return_value = snapshot(
        "m1", text="A", memory_hash="hash-a", status="pending"
    )
    job_store.active_matches.return_value = False

    assert service.after_update("m1", owner_id=OWNER_ID, text_changed=False) is True

    job_store.active_matches.assert_called_once_with(
        "m1", "hash-a", "job-for-b", owner_id=OWNER_UUID
    )
    job_store.prepare.assert_called_once()
    generation = memory_store.mark_pending.call_args.args[1]
    memory_store.mark_pending.assert_called_once_with(
        "m1",
        generation,
        owner_id=OWNER_UUID,
        expected_hash="hash-a",
        expected_text="A",
        expected_generation="job-for-b",
        expected_origin=None,
    )
    assert job_store.prepare.call_args.args[1] == "hash-a"


def test_sequential_same_text_completed_put_remains_a_noop(service, memory_store, job_store):
    memory_store.get.return_value = snapshot("m1", text="A", memory_hash="hash-a", status="completed")

    assert service.after_update("m1", owner_id=OWNER_ID, text_changed=False) is True

    memory_store.mark_pending.assert_not_called()
    job_store.enqueue.assert_not_called()


def test_put_reconciliation_reuses_fence_session_for_catalog_and_job_rows(
    service, memory_store, job_store, catalog_store
):
    fenced_session = object()
    job_store.memory_fence.return_value = nullcontext(fenced_session)
    memory_store.get.return_value = snapshot("m1", text="Changed", status="completed")
    memory_store.mark_pending.return_value = snapshot("m1", text="Changed", status="pending")
    catalog_store.get_saved.return_value = PROJECT

    assert service.after_update("m1", owner_id=OWNER_ID, text_changed=True) is True

    catalog_store.get_saved.assert_called_once_with(OWNER_ID, session=fenced_session)
    assert job_store.prepare.call_args.kwargs["session"] is fenced_session
    assert job_store.install_prepared.call_args.kwargs["session"] is fenced_session


def test_reconcile_pending_replaces_only_orphaned_payload_generations(
    service, memory_store, job_store, monkeypatch
):
    """A restart must repair the mark-before-enqueue crash window without duplicating bound work."""
    memory_store.iter_snapshots.return_value = [
        snapshot("bound", text="Bound", status="pending", generation="bound-job"),
        snapshot("orphan", text="Orphan", status="pending", generation="missing-job"),
        snapshot("legacy", text="Legacy", status="pending"),
        snapshot("done", text="Done", status="completed"),
    ]
    job_store.active_matches.side_effect = [True, False]
    snapshots = {item.memory_id: item for item in memory_store.iter_snapshots.return_value}
    memory_store.get.side_effect = lambda memory_id: snapshots[memory_id]
    memory_store.mark_pending.side_effect = lambda memory_id, generation, **_kwargs: snapshot(
        memory_id,
        text=snapshots[memory_id].text,
        status="pending",
        generation=generation,
    )
    job_store.active_matches.side_effect = (
        lambda _memory_id, _memory_hash, generation, **_kwargs: generation == "bound-job"
    )

    assert service.reconcile_pending() == 2

    assert [call.args[0] for call in job_store.prepare.call_args_list] == ["orphan", "legacy"]


def test_reconcile_pending_continues_after_one_memory_fails(
    service, memory_store, job_store, monkeypatch, caplog
):
    memory_store.iter_snapshots.return_value = [
        snapshot("broken", status="pending", generation="missing-a"),
        snapshot("repaired", status="pending", generation="missing-b"),
    ]
    snapshots = {item.memory_id: item for item in memory_store.iter_snapshots.return_value}
    memory_store.get.side_effect = lambda memory_id: snapshots[memory_id]
    memory_store.mark_pending.side_effect = lambda memory_id, generation, **_kwargs: snapshot(
        memory_id, status="pending", generation=generation
    )
    job_store.prepare.side_effect = [
        RuntimeError("secret storage detail"),
        EnqueueResult(job_id=uuid.uuid4(), created=True),
    ]

    assert service.reconcile_pending() == 1
    assert [call.args[0] for call in job_store.prepare.call_args_list] == ["broken", "repaired"]
    assert "category_enqueue_failed" in caplog.text
    assert "secret storage detail" not in caplog.text


def test_restart_installs_the_exact_prepared_catalog_named_by_pending_marker(
    service, memory_store, job_store, catalog_store
):
    """Crash after marker must activate its durable request catalog, never current project defaults."""
    generation = str(uuid.uuid4())
    pending = snapshot("m1", text="Same", status="pending", generation=generation)
    memory_store.iter_snapshots.return_value = [pending]
    memory_store.get.return_value = pending
    job_store.memory_fence.return_value = nullcontext()
    job_store.active_matches.return_value = False
    job_store.prepared_matches.return_value = True
    job_store.install_prepared.return_value = True

    assert service.reconcile_pending() == 1

    job_store.install_prepared.assert_called_once_with(uuid.UUID(generation), OWNER_UUID)
    job_store.prepare.assert_not_called()
    catalog_store.get_saved.assert_not_called()


def test_restart_finishes_origin_bound_preparation_that_crashed_before_pending_marker(
    service, memory_store, job_store
):
    """The request token reconnects the vector row to its already-durable per-call catalog job."""
    origin = str(uuid.uuid4())
    current = snapshot("m1", text="Same", status="completed", categories=("billing",), origin=origin)
    memory_store.iter_snapshots.return_value = [current]
    memory_store.get.return_value = current
    memory_store.mark_pending.return_value = snapshot("m1", text="Same", status="pending")
    job_store.memory_fence.return_value = nullcontext()
    job_store.prepared_matches.return_value = True
    job_store.install_prepared.return_value = True

    assert service.reconcile_pending() == 1

    generation = memory_store.mark_pending.call_args.args[1]
    assert job_store.prepared_matches.call_args.args == ("m1", "h1", generation)
    assert job_store.prepared_matches.call_args.kwargs["owner_id"] == OWNER_UUID
    assert memory_store.mark_pending.call_args.kwargs == {
        "owner_id": OWNER_UUID,
        "expected_hash": "h1",
        "expected_text": "Same",
        "expected_generation": None,
        "expected_origin": origin,
    }
    job_store.install_prepared.assert_called_once_with(uuid.UUID(generation), OWNER_UUID)


def test_restart_sweeps_non_origin_preparation_before_any_vector_marker(service, memory_store, job_store):
    """A crash after durable prepare must not depend on pending/origin vector scans."""
    generation = uuid.uuid4()
    job_store.list_prepared.return_value = (
        PreparedCategoryJob(
            id=generation, memory_id="m1", owner_id=OWNER_UUID, memory_hash="h1"
        ),
    )
    memory_store.iter_snapshots.return_value = []
    memory_store.get.return_value = snapshot("m1", text="Changed text", memory_hash="h1", status="completed")
    memory_store.mark_pending.return_value = snapshot(
        "m1", text="Changed text", memory_hash="h1", status="pending", generation=str(generation)
    )
    job_store.install_prepared.return_value = True

    assert service.reconcile_pending() == 1

    memory_store.mark_pending.assert_called_once_with(
        "m1",
        str(generation),
        owner_id=OWNER_UUID,
        expected_hash="h1",
        expected_text="Changed text",
        expected_generation=None,
        expected_origin=None,
    )
    job_store.install_prepared.assert_called_once_with(generation, OWNER_UUID)


def test_restart_quarantines_a_preparation_whose_owner_does_not_match_the_memory(
    service, memory_store, job_store
):
    """Startup recovery must not attach one owner's durable token to another owner's memory."""
    generation = uuid.uuid4()
    job_store.list_prepared.return_value = (
        PreparedCategoryJob(
            id=generation,
            memory_id="m1",
            owner_id=uuid.UUID(OWNER_ID),
            memory_hash="h1",
        ),
    )
    memory_store.get.return_value = snapshot(
        "m1",
        user_id="00000000-0000-0000-0000-000000000002",
        memory_hash="h1",
        status="completed",
    )

    assert service.reconcile_pending() == 0

    job_store.cancel_prepared.assert_called_once_with(generation, OWNER_UUID)
    memory_store.mark_pending.assert_not_called()
    job_store.install_prepared.assert_not_called()


@pytest.mark.parametrize(
    "current",
    [
        snapshot("m1", status="pending", generation=str(uuid.uuid4())),
        snapshot("m1", status="completed", generation=None),
    ],
    ids=["newer-active", "newer-completed"],
)
def test_restart_cancels_an_old_preparation_superseded_by_newer_work(
    service, memory_store, job_store, current
):
    old_preparation = PreparedCategoryJob(
        id=uuid.uuid4(), memory_id="m1", owner_id=OWNER_UUID, memory_hash="h1"
    )
    job_store.list_prepared.return_value = (old_preparation,)
    job_store.preparation_is_latest.return_value = False
    memory_store.get.return_value = current

    assert service.reconcile_pending() == 0

    job_store.cancel_prepared.assert_called_once_with(old_preparation.id, OWNER_UUID)
    memory_store.mark_pending.assert_not_called()


def test_restart_falls_back_for_an_origin_marker_without_a_prepared_job(
    service, memory_store, job_store, catalog_store
):
    """A crash before durable prepare must not leave the private request marker forever."""
    origin = str(uuid.uuid4())
    current = snapshot("m1", text="Same", status="completed", origin=origin)
    memory_store.iter_snapshots.return_value = [current]
    memory_store.get.return_value = current
    memory_store.mark_pending.return_value = snapshot("m1", text="Same", status="pending")
    job_store.prepared_matches.return_value = False
    catalog_store.get_saved.return_value = PROJECT

    assert service.reconcile_pending() == 1

    assert job_store.prepare.call_count == 1
    assert memory_store.mark_pending.call_args.kwargs["expected_origin"] == origin


def test_failed_origin_install_retains_generation_and_prepared_job_for_next_restart(
    service, memory_store, job_store
):
    origin = str(uuid.uuid4())
    generation = service._generation_for_origin(origin, "m1")
    current = snapshot("m1", text="Same", status="completed", origin=origin)
    pending = snapshot("m1", text="Same", status="pending", generation=str(generation))
    memory_store.iter_snapshots.return_value = [current]
    memory_store.get.return_value = current
    memory_store.mark_pending.return_value = pending
    memory_store.write_result.return_value = False
    job_store.prepared_matches.return_value = True
    job_store.install_prepared.return_value = False

    assert service.reconcile_pending() == 0

    memory_store.write_result.assert_called_once_with(
        "m1", "h1", str(generation), [], "failed", owner_id=OWNER_UUID
    )
    job_store.cancel_prepared.assert_not_called()
    memory_store.restore.assert_not_called()


def test_reconcile_lock_failure_logs_the_observed_memory_id(service, memory_store, job_store, caplog):
    memory_store.iter_snapshots.return_value = [snapshot("broken", status="pending")]
    job_store.memory_fence.side_effect = RuntimeError("pool unavailable")

    assert service.reconcile_pending() == 0

    assert "memory_id=broken" in caplog.text


def test_install_failure_terminalizes_new_text_instead_of_restoring_old_categories(
    service, memory_store, job_store
):
    old = snapshot("m1", text="New text", status="completed", categories=("billing",))
    memory_store.get.return_value = old
    reservation = EnqueueResult(job_id=uuid.uuid4(), created=True)
    job_store.prepare.return_value = reservation
    memory_store.mark_pending.return_value = snapshot(
        "m1", text="New text", status="pending", generation=str(reservation.job_id)
    )
    job_store.install_prepared.return_value = False
    memory_store.write_result.return_value = True

    assert service.enqueue_memory(
        "m1", EffectiveCatalog(definitions=PROJECT, source="project"), replace_active=True
    ) is False

    memory_store.restore.assert_not_called()
    memory_store.write_result.assert_called_once_with(
        "m1", "h1", str(reservation.job_id), [], "failed", owner_id=OWNER_UUID
    )


def test_after_add_absorbs_category_failures(service, memory_store, monkeypatch):
    """Category work is auxiliary, so an internal failure must not fail core memory ingestion."""
    monkeypatch.setattr(
        service,
        "_enqueue_snapshot_locked",
        MagicMock(side_effect=RuntimeError("raw provider response")),
    )
    memory_store.get.return_value = snapshot("a", text="A")
    response = {"results": [{"id": "a", "event": "ADD", "memory": "A"}]}

    assert service.after_add(response, EffectiveCatalog(definitions=PROJECT, source="project"))["results"][0]["category_status"] == "failed"


def test_run_memory_update_reports_a_swallowed_category_failure(service, monkeypatch):
    """MCP needs an explicit outcome when reconciliation fails after the core update succeeds."""
    monkeypatch.setattr(service, "_after_update_locked", MagicMock(side_effect=RuntimeError("provider secret")))

    outcome = service.run_memory_update(
        "m1",
        lambda: {"id": "m1", "memory": "updated"},
        owner_id=OWNER_ID,
        supplied_text="updated",
        with_category_outcome=True,
    )

    assert outcome == CategoryUpdateOutcome(
        response={"id": "m1", "memory": "updated"},
        category_processing_failed=True,
    )


def test_run_memory_update_reports_a_false_category_reconciliation_result(service, monkeypatch):
    """A false result means the core write survived but its category work did not complete."""
    monkeypatch.setattr(service, "_after_update_locked", MagicMock(return_value=False))

    outcome = service.run_memory_update(
        "m1",
        lambda: {"id": "m1", "memory": "updated"},
        owner_id=OWNER_ID,
        supplied_text="updated",
        with_category_outcome=True,
    )

    assert outcome == CategoryUpdateOutcome(
        response={"id": "m1", "memory": "updated"},
        category_processing_failed=True,
    )


def test_after_add_prepare_failure_persists_exact_memory_as_failed(catalog_store, classifier):
    origin = str(uuid.uuid4())
    vector = AtomicVectorStore(
        {
            "data": "A",
            "hash": "h1",
            "categories": ["stale"],
            "category_status": "completed",
            "_category_generation": None,
            "_category_origin": origin,
        }
    )
    memory_store = MemoryCategoryStore(lambda: SimpleNamespace(vector_store=vector))
    job_store = MagicMock(name="job_store")
    job_store.memory_fence.return_value = nullcontext()
    job_store.prepare.side_effect = RuntimeError("database unavailable")
    service = CategoryService(catalog_store, job_store, memory_store, classifier)

    result = service.after_add(
        {"results": [{"id": "m1", "event": "ADD", "memory": "A"}]},
        EffectiveCatalog(definitions=PROJECT, source="project"),
        origin_token=origin,
    )

    assert result["results"][0]["category_status"] == "failed"
    assert vector.payload["categories"] == []
    assert vector.payload["category_status"] == "failed"
    assert vector.payload["_category_generation"] is None
    assert vector.payload["_category_origin"] is None


def test_changed_text_put_prepare_failure_persists_failed_without_blocking_core_success(
    catalog_store, classifier
):
    vector = AtomicVectorStore(
        {
            "data": "Old",
            "hash": "h1",
            "categories": ["billing"],
            "category_status": "completed",
            "_category_generation": None,
            "_category_origin": None,
        }
    )
    memory_store = MemoryCategoryStore(lambda: SimpleNamespace(vector_store=vector))
    job_store = MagicMock(name="job_store")
    job_store.memory_fence.return_value = nullcontext()
    job_store.prepare.side_effect = RuntimeError("database unavailable")
    service = CategoryService(catalog_store, job_store, memory_store, classifier)

    def core_update():
        with vector._lock:
            vector.payload.update(
                {
                    "data": "New",
                    "hash": "h2",
                    "categories": ["billing"],
                    "category_status": "completed",
                }
            )
        return {"id": "m1", "memory": "New"}

    response = service.run_memory_update("m1", core_update, owner_id=OWNER_ID, supplied_text="New")

    assert response == {"id": "m1", "memory": "New"}
    assert vector.payload["data"] == "New"
    assert vector.payload["hash"] == "h2"
    assert vector.payload["categories"] == []
    assert vector.payload["category_status"] == "failed"


def test_after_text_update_replaces_an_active_job_with_the_current_catalog(service, catalog_store, job_store):
    """Keeping the old snapshot after a text edit would classify the new text against stale rules."""
    catalog_store.get_saved.return_value = PROJECT

    assert service.after_text_update("m1", owner_id=OWNER_ID) is True

    job_store.prepare.assert_called_once()


def test_after_delete_cancels_active_work_for_one_memory(service, job_store):
    """An uncancelled job could write category payload after its memory is gone."""
    assert service.after_delete("m1", OWNER_ID) is True
    job_store.cancel_active.assert_called_once_with("m1", OWNER_UUID)


def test_after_owner_reset_purges_all_of_only_that_owners_jobs(service, job_store):
    """Resetting one account must erase its job history without touching another account."""
    assert service.after_owner_reset(OWNER_ID) is True
    job_store.purge_owner.assert_called_once_with(uuid.UUID(OWNER_ID))


def test_preview_and_start_share_scope_and_cost_math(service, classifier, memory_store, job_store):
    """Drift between dry-run and execution would make operator estimates untrustworthy."""
    memory_store.iter_snapshots.return_value = [
        snapshot("unclassified", text="One", status="unclassified"),
        snapshot("failed", text="Two", status="failed"),
        snapshot("completed", text="Three", status="completed"),
    ]
    classifier.estimate_tokens.return_value = (100, 20)

    preview = service.preview_reclassification(
        scope="unclassified_failed", owner_id=OWNER_ID, input_rate_per_million=2.0, output_rate_per_million=8.0
    )
    started = service.start_reclassification(scope="unclassified_failed", confirm="RECLASSIFY", owner_id=OWNER_ID)

    assert preview.eligible_memories == 2
    assert preview.estimated_calls == 2
    assert preview.estimated_input_tokens == 200
    assert preview.estimated_output_tokens == 40
    assert preview.estimated_cost == pytest.approx(0.00072)
    assert started.eligible_memories == preview.eligible_memories
    assert started.created_jobs == 2
    assert started.skipped_active_jobs == 0
    classifier.estimate_tokens.assert_called()
    assert job_store.prepare.call_count == 2


def test_preview_without_both_rates_returns_no_cost_and_never_classifies(service, classifier, memory_store):
    """Dry run must budget locally and never trigger an LLM call or invented provider pricing."""
    memory_store.iter_snapshots.return_value = [snapshot("m1", status="completed")]
    classifier.estimate_tokens.return_value = (4, 2)

    preview = service.preview_reclassification(scope="all", owner_id=OWNER_ID, input_rate_per_million=1.0)

    assert preview.eligible_memories == 1
    assert preview.estimated_cost is None
    classifier.classify.assert_not_called()


def test_start_reclassification_requires_the_exact_confirmation(service, memory_store, job_store):
    """A permissive confirmation check could initiate costly historical processing accidentally."""
    memory_store.iter_snapshots.return_value = [snapshot("m1")]

    with pytest.raises(ValueError, match="RECLASSIFY"):
        service.start_reclassification(scope="all", confirm="reclassify", owner_id=OWNER_ID)

    job_store.enqueue.assert_not_called()


def test_start_reclassification_skips_one_active_job_idempotently(service, memory_store, job_store):
    """Repeated execution must not create a second active job for the same memory."""
    memory_store.iter_snapshots.return_value = [snapshot("m1"), snapshot("m2")]
    active_job = uuid.uuid4()
    snapshots = {
        "m1": snapshot("m1", status="pending", generation=str(active_job)),
        "m2": snapshot("m2"),
    }
    memory_store.get.side_effect = lambda memory_id: snapshots[memory_id]
    memory_store.mark_pending.side_effect = lambda memory_id, generation, **_kwargs: snapshot(
        memory_id, status="pending", generation=generation
    )
    job_store.active_matches.side_effect = lambda memory_id, *_args, **_kwargs: memory_id == "m1"

    result = service.start_reclassification(scope="all", confirm="RECLASSIFY", owner_id=OWNER_ID)

    assert result.created_jobs == 1
    assert result.skipped_active_jobs == 1
    assert result.eligible_memories == 2


@pytest.mark.parametrize("scope", ["", "failed", "everything"])
def test_reclassification_rejects_unknown_scope(service, scope):
    """Accepting an unknown scope could silently process an operator's unintended set of memories."""
    with pytest.raises(ValueError, match="scope"):
        service.preview_reclassification(scope=scope, owner_id=OWNER_ID)
