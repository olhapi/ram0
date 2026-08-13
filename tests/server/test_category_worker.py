"""Contract tests for the single-threaded durable category worker runtime."""

import threading
import uuid
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

pytest.importorskip("fastapi")

import category_runtime
from category_classifier import CategoryResultError
from category_models import CategoryDefinition, CategoryJobState
from category_store import CategoryJobStore, ClaimedCategoryJob, MemorySnapshot
from category_worker import CategoryWorker
from models import CategoryJob


NOW = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
JOB_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
OWNER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
OTHER_OWNER_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")
CATALOG = (CategoryDefinition(name="billing", description="Invoices and payments."),)


def claimed_job(
    *,
    owner_id=OWNER_ID,
    memory_hash="h1",
    attempts=1,
    terminalizing=False,
    terminal_error_code=None,
    terminal_error_message=None,
):
    """Build the immutable data returned only after a durable lease commits."""
    return ClaimedCategoryJob(
        id=JOB_ID,
        memory_id="mem-1",
        owner_id=owner_id,
        memory_hash=memory_hash,
        catalog=CATALOG,
        attempts=attempts,
        terminalizing=terminalizing,
        terminal_error_code=terminal_error_code,
        terminal_error_message=terminal_error_message,
    )


def snapshot(*, owner_id=OWNER_ID, memory_hash="h1", text="Invoice memory", generation=str(JOB_ID)):
    """Build the category fields a worker must re-read before each classification."""
    return MemorySnapshot(
        memory_id="mem-1",
        user_id=str(owner_id),
        text=text,
        memory_hash=memory_hash,
        categories=None,
        category_status="pending",
        category_generation=generation,
        payload={},
    )


@pytest.fixture
def job_store():
    store = MagicMock(name="job_store")
    store.claim.return_value = claimed_job()
    store.memory_fence.side_effect = lambda _memory_id: nullcontext()
    store.renew.return_value = True
    return store


@pytest.fixture
def memory_store():
    store = MagicMock(name="memory_store")
    store.get.return_value = snapshot()
    store.write_result.return_value = True
    return store


@pytest.fixture
def classifier():
    classifier = MagicMock(name="classifier")
    classifier.classify.return_value = ["billing"]
    return classifier


@pytest.fixture
def worker(job_store, memory_store, classifier):
    return CategoryWorker(
        job_store,
        memory_store,
        classifier,
        worker_id="worker-test",
        poll_seconds=0.01,
        lease_seconds=60,
        now=lambda: NOW,
    )


def test_success_writes_categories_then_completes(worker, classifier, memory_store, job_store):
    """Completing before the payload write could permanently lose the category result."""
    assert worker.process_once() is True

    classifier.classify.assert_called_once_with("Invoice memory", CATALOG)
    memory_store.write_result.assert_called_once_with(
        "mem-1", "h1", str(JOB_ID), ["billing"], "completed", owner_id=OWNER_ID
    )
    job_store.complete.assert_called_once_with(
        JOB_ID, worker.worker_id, owner_id=OWNER_ID, now=NOW, lease_fenced=True
    )


def test_no_ready_job_does_not_classify_or_write(worker, classifier, memory_store, job_store):
    """An empty poll must remain a cheap no-op so idle threads can poll safely."""
    job_store.claim.return_value = None

    assert worker.process_once() is False

    classifier.classify.assert_not_called()
    memory_store.write_result.assert_not_called()


def test_worker_quarantines_a_job_whose_owner_does_not_match_the_memory(
    worker, classifier, memory_store, job_store
):
    """A durable job/token collision must never classify another owner's memory."""
    job_store.claim.return_value = claimed_job(owner_id=OWNER_ID)
    memory_store.get.return_value = snapshot(owner_id=OTHER_OWNER_ID)

    assert worker.process_once() is True

    classifier.classify.assert_not_called()
    memory_store.mark_pending.assert_not_called()
    memory_store.write_result.assert_not_called()
    job_store.cancel.assert_called_once_with(
        JOB_ID,
        worker.worker_id,
        "owner_mismatch",
        owner_id=OWNER_ID,
        now=NOW,
        lease_fenced=True,
    )


@pytest.mark.parametrize(
    ("memory", "reason"),
    [(None, "memory_deleted"), (snapshot(memory_hash="h2"), "replaced")],
)
def test_missing_or_changed_memory_cancels_claim(worker, memory_store, job_store, memory, reason):
    """A leased job may never classify or write a deleted or text-replaced memory."""
    memory_store.get.return_value = memory

    assert worker.process_once() is True

    job_store.cancel.assert_called_once_with(
        JOB_ID, worker.worker_id, reason, owner_id=OWNER_ID, now=NOW, lease_fenced=True
    )
    memory_store.write_result.assert_not_called()


def test_stale_payload_write_cancels_without_completing(worker, memory_store, job_store):
    """A hash change between read and write must leave the late worker unable to complete."""
    memory_store.write_result.return_value = False

    assert worker.process_once() is True

    job_store.cancel.assert_called_once_with(
        JOB_ID, worker.worker_id, "replaced", owner_id=OWNER_ID, now=NOW, lease_fenced=True
    )
    job_store.complete.assert_not_called()


def test_renewed_old_worker_uses_job_generation_cas_before_writing(worker, memory_store, job_store):
    memory_store.write_result.return_value = False

    assert worker.process_once() is True

    memory_store.write_result.assert_called_once_with(
        "mem-1", "h1", str(JOB_ID), ["billing"], "completed", owner_id=OWNER_ID
    )
    job_store.cancel.assert_called_once_with(
        JOB_ID, worker.worker_id, "replaced", owner_id=OWNER_ID, now=NOW, lease_fenced=True
    )


def test_restarted_staged_job_binds_missing_generation_before_classification(
    worker, memory_store, classifier, job_store
):
    memory_store.get.return_value = snapshot(generation=None)
    memory_store.mark_pending.return_value = snapshot(generation=str(JOB_ID))

    assert worker.process_once() is True

    memory_store.mark_pending.assert_called_once_with(
        "mem-1",
        str(JOB_ID),
        owner_id=OWNER_ID,
        expected_hash="h1",
        expected_text="Invoice memory",
        expected_generation=None,
        expected_origin=None,
    )
    classifier.classify.assert_called_once()
    job_store.complete.assert_called_once()


def test_slow_classification_never_writes_after_its_lease_expired(job_store, memory_store, classifier):
    """A slow model call must renew at a fresh instant or leave a reclaimed payload untouched."""
    after_lease = NOW + timedelta(seconds=61)
    clock = MagicMock(side_effect=[NOW, after_lease])
    worker = CategoryWorker(
        job_store,
        memory_store,
        classifier,
        worker_id="worker-test",
        lease_seconds=60,
        now=clock,
    )
    job_store.renew.return_value = False

    assert worker.process_once() is True

    job_store.claim.assert_called_once_with(worker.worker_id, NOW, 60)
    job_store.renew.assert_called_once_with(
        JOB_ID, worker.worker_id, owner_id=OWNER_ID, now=after_lease, lease_seconds=60
    )
    memory_store.write_result.assert_not_called()
    job_store.complete.assert_not_called()


def test_classifier_runs_outside_the_postgres_memory_fence(job_store, memory_store, classifier):
    held = False

    @contextmanager
    def fence(_memory_id):
        nonlocal held
        held = True
        try:
            yield None
        finally:
            held = False

    job_store.memory_fence.side_effect = fence
    classifier.classify.side_effect = lambda *_args: [] if not held else pytest.fail("LLM called under fence")
    job_store.renew.return_value = True
    worker = CategoryWorker(job_store, memory_store, classifier, worker_id="worker-test", now=lambda: NOW)

    assert worker.process_once() is True


def test_worker_threads_each_fence_session_into_its_job_mutations(job_store, memory_store, classifier):
    sessions = [object(), object()]

    @contextmanager
    def fence(_memory_id):
        yield sessions.pop(0)

    job_store.memory_fence.side_effect = fence
    worker = CategoryWorker(job_store, memory_store, classifier, worker_id="worker-test", now=lambda: NOW)

    assert worker.process_once() is True

    assert job_store.renew.call_args_list[0].kwargs["session"] is not None
    assert job_store.renew.call_args_list[-1].kwargs["session"] is not None
    assert job_store.complete.call_args.kwargs["session"] is job_store.renew.call_args_list[-1].kwargs["session"]


def test_slow_classifier_heartbeats_during_the_model_call(job_store, memory_store, classifier):
    entered = threading.Event()
    release = threading.Event()
    job_store.renew.return_value = True

    def slow_classify(*_args):
        entered.set()
        assert release.wait(1.0)
        return ["billing"]

    classifier.classify.side_effect = slow_classify
    worker = CategoryWorker(
        job_store,
        memory_store,
        classifier,
        worker_id="worker-test",
        lease_seconds=0.06,
        now=lambda: NOW,
    )
    thread = threading.Thread(target=worker.process_once)
    thread.start()
    assert entered.wait(1.0)
    assert threading.Event().wait(0.08) is False

    assert job_store.renew.call_count >= 2
    release.set()
    thread.join(1.0)
    assert not thread.is_alive()


def test_slow_final_vector_write_cannot_be_reclaimed_or_classified_twice(classifier):
    """Reclaim takes the memory fence before its row lock, so final CAS owns the attempt."""
    class CoordinatedJobStore:
        def __init__(self):
            self.lock = threading.Lock()
            self.state = "queued"
            self.worker_id = None

        @contextmanager
        def memory_fence(self, _memory_id):
            with self.lock:
                yield object()

        def claim(self, worker_id, _now, _lease_seconds):
            if self.state == "completed":
                return None
            with self.memory_fence("mem-1"):
                if self.state == "completed":
                    return None
                self.state = "processing"
                self.worker_id = worker_id
                return claimed_job()

        def renew(self, _job_id, worker_id, **_kwargs):
            return self.state == "processing" and self.worker_id == worker_id

        def complete(self, _job_id, worker_id, **_kwargs):
            if self.worker_id != worker_id:
                return False
            self.state = "completed"
            return True

        def cancel(self, *_args, **_kwargs):
            self.state = "cancelled"
            return True

    entered_write = threading.Event()
    release_write = threading.Event()
    writes = []
    memory_store = MagicMock()
    memory_store.get.return_value = snapshot()

    def slow_write(*args, **_kwargs):
        writes.append(args)
        entered_write.set()
        assert release_write.wait(1.0)
        return True

    memory_store.write_result.side_effect = slow_write
    classifier.classify.return_value = ["billing"]
    store = CoordinatedJobStore()
    first = CategoryWorker(store, memory_store, classifier, worker_id="worker-a", now=lambda: NOW)
    reclaimer = CategoryWorker(store, memory_store, classifier, worker_id="worker-b", now=lambda: NOW)
    first_thread = threading.Thread(target=first.process_once)
    reclaim_thread = threading.Thread(target=reclaimer.process_once)

    first_thread.start()
    assert entered_write.wait(1.0)
    reclaim_thread.start()
    assert threading.Event().wait(0.05) is False
    assert classifier.classify.call_count == 1
    assert len(writes) == 1

    release_write.set()
    first_thread.join(1.0)
    reclaim_thread.join(1.0)
    assert not first_thread.is_alive()
    assert not reclaim_thread.is_alive()
    assert classifier.classify.call_count == 1
    assert len(writes) == 1


def test_worker_uses_fresh_timestamps_for_cancellation_retry_and_completion(job_store, memory_store, classifier):
    """Mutation timestamps must describe the actual operation, not the earlier claim instant."""
    claim_time = NOW
    cancel_time = NOW + timedelta(seconds=1)
    retry_time = NOW + timedelta(seconds=2)
    renew_time = NOW + timedelta(seconds=3)
    complete_time = NOW + timedelta(seconds=4)

    cancelled = CategoryWorker(
        job_store,
        memory_store,
        classifier,
        worker_id="worker-test",
        now=MagicMock(side_effect=[claim_time, cancel_time]),
    )
    memory_store.get.return_value = None
    assert cancelled.process_once() is True
    job_store.cancel.assert_called_once_with(
        JOB_ID,
        cancelled.worker_id,
        "memory_deleted",
        owner_id=OWNER_ID,
        now=cancel_time,
        lease_fenced=True,
    )

    job_store.reset_mock()
    memory_store.reset_mock()
    memory_store.get.return_value = snapshot()
    classifier.classify.side_effect = CategoryResultError("invalid_json", "Invalid category response")
    job_store.reschedule_or_fail.return_value = CategoryJobState.RETRYING
    retrying = CategoryWorker(
        job_store,
        memory_store,
        classifier,
        worker_id="worker-test",
        now=MagicMock(side_effect=[claim_time, claim_time + timedelta(seconds=1), retry_time]),
    )
    assert retrying.process_once() is True
    assert job_store.reschedule_or_fail.call_args.kwargs["now"] == retry_time

    job_store.reset_mock()
    memory_store.reset_mock()
    memory_store.get.return_value = snapshot()
    memory_store.write_result.return_value = True
    classifier.classify.side_effect = None
    classifier.classify.return_value = ["billing"]
    completing = CategoryWorker(
        job_store,
        memory_store,
        classifier,
        worker_id="worker-test",
        now=MagicMock(side_effect=[claim_time, renew_time, renew_time, complete_time]),
    )
    assert completing.process_once() is True
    assert job_store.renew.call_count == 2
    assert {call.kwargs["now"] for call in job_store.renew.call_args_list} == {renew_time}
    job_store.complete.assert_called_once_with(
        JOB_ID,
        completing.worker_id,
        owner_id=OWNER_ID,
        now=complete_time,
        lease_fenced=True,
    )


def test_safe_classifier_error_retries_without_writing_terminal_payload(worker, classifier, memory_store, job_store, caplog):
    """Retries retain pending state; only an exhausted job may expose failed categories."""
    classifier.classify.side_effect = CategoryResultError("invalid_json", "Invalid category response")
    job_store.reschedule_or_fail.return_value = CategoryJobState.RETRYING

    assert worker.process_once() is True

    job_store.reschedule_or_fail.assert_called_once_with(
        JOB_ID,
        worker.worker_id,
        owner_id=OWNER_ID,
        now=NOW,
        error_code="invalid_json",
        error_message="Invalid category response",
        max_attempts=3,
    )
    memory_store.write_result.assert_not_called()
    assert "category_worker_job_retrying" in caplog.text
    assert "job_id=11111111-1111-1111-1111-111111111111" in caplog.text
    assert "memory_id=mem-1" in caplog.text


def test_malformed_result_marks_current_memory_failed_only_after_exhaustion(worker, classifier, memory_store, job_store):
    """Terminal classifier failures must show a stable failed result without raw provider data."""
    classifier.classify.side_effect = CategoryResultError("invalid_json", "Invalid category response")
    job_store.reschedule_or_fail.return_value = CategoryJobState.PROCESSING

    assert worker.process_once() is True

    memory_store.write_result.assert_called_once_with(
        "mem-1", "h1", str(JOB_ID), [], "failed", owner_id=OWNER_ID
    )
    job_store.fail.assert_called_once_with(
        JOB_ID,
        worker.worker_id,
        owner_id=OWNER_ID,
        now=NOW,
        error_code="invalid_json",
        error_message="Invalid category response",
        lease_fenced=True,
    )


def test_terminal_payload_exception_keeps_job_active_for_lease_recovery(worker, classifier, memory_store, job_store):
    classifier.classify.side_effect = CategoryResultError("invalid_json", "Invalid category response")
    job_store.reschedule_or_fail.return_value = CategoryJobState.PROCESSING
    memory_store.write_result.side_effect = RuntimeError("vector store unavailable")

    assert worker.process_once() is True

    job_store.fail.assert_not_called()
    job_store.cancel.assert_not_called()


def test_reclaimed_terminalizing_attempt_never_calls_classifier(
    worker, classifier, memory_store, job_store
):
    job_store.claim.return_value = claimed_job(
        attempts=3,
        terminalizing=True,
        terminal_error_code="invalid_json",
        terminal_error_message="Invalid category response",
    )

    assert worker.process_once() is True

    classifier.classify.assert_not_called()
    memory_store.write_result.assert_called_once_with(
        "mem-1", "h1", str(JOB_ID), [], "failed", owner_id=OWNER_ID
    )
    job_store.fail.assert_called_once_with(
        JOB_ID,
        worker.worker_id,
        owner_id=OWNER_ID,
        now=NOW,
        error_code="invalid_json",
        error_message="Invalid category response",
        lease_fenced=True,
    )


def test_repeated_terminal_payload_failures_back_off_without_more_llm_calls(
    worker, classifier, memory_store, job_store
):
    job_store.claim.return_value = claimed_job(
        attempts=3,
        terminalizing=True,
        terminal_error_code="provider_error",
        terminal_error_message="Category provider request failed",
    )
    memory_store.write_result.side_effect = RuntimeError("vector store unavailable")

    assert worker.process_once() is True
    assert worker.process_once() is True

    classifier.classify.assert_not_called()
    assert memory_store.write_result.call_count == 2
    assert job_store.claim.return_value.attempts == 3
    assert job_store.reschedule_terminalization.call_count == 2
    for call in job_store.reschedule_terminalization.call_args_list:
        assert call.args == (JOB_ID, worker.worker_id)
        assert call.kwargs["owner_id"] == OWNER_ID
        assert call.kwargs["now"] == NOW
        assert call.kwargs["max_backoff_seconds"] == 60
        assert call.kwargs["lease_fenced"] is True


def test_terminal_stale_payload_cancels_instead_of_committing_failed_job(worker, classifier, memory_store, job_store):
    classifier.classify.side_effect = CategoryResultError("invalid_json", "Invalid category response")
    job_store.reschedule_or_fail.return_value = CategoryJobState.PROCESSING
    memory_store.write_result.return_value = False

    assert worker.process_once() is True

    job_store.fail.assert_not_called()
    job_store.cancel.assert_called_once_with(
        JOB_ID, worker.worker_id, "replaced", owner_id=OWNER_ID, now=NOW, lease_fenced=True
    )


def test_unexpected_errors_are_sanitized_and_retry_with_the_generic_code(worker, classifier, memory_store, job_store, caplog):
    """Worker logs must retain identifiers while never exposing memory/provider/credential text."""
    secret = "MEMORY_SECRET_should_not_log"
    response = "RAW_PROVIDER_RESPONSE_should_not_log"
    classifier.classify.side_effect = RuntimeError(f"{secret} {response}")
    job_store.reschedule_or_fail.return_value = CategoryJobState.RETRYING

    assert worker.process_once() is True

    assert "category_error" in caplog.text
    assert "mem-1" in caplog.text
    assert secret not in caplog.text
    assert response not in caplog.text
    job_store.reschedule_or_fail.assert_called_once_with(
        JOB_ID,
        worker.worker_id,
        owner_id=OWNER_ID,
        now=NOW,
        error_code="category_error",
        error_message="Category classification failed",
        max_attempts=3,
    )


def test_unexpected_terminal_error_uses_hash_guarded_failed_write(worker, classifier, memory_store, job_store):
    """Generic failures follow the same terminal lifecycle as safe classifier failures."""
    classifier.classify.side_effect = RuntimeError("raw provider response")
    job_store.reschedule_or_fail.return_value = CategoryJobState.PROCESSING

    assert worker.process_once() is True

    memory_store.write_result.assert_called_once_with(
        "mem-1", "h1", str(JOB_ID), [], "failed", owner_id=OWNER_ID
    )


def test_one_daemon_thread_has_only_one_in_flight_classification_and_stop_is_truthful(
    worker, classifier, job_store
):
    """A stop timeout must report a still-running provider call rather than claiming shutdown succeeded."""
    entered = threading.Event()
    release = threading.Event()
    job_store.claim.side_effect = [claimed_job(), None]

    def block_classification(*_args):
        entered.set()
        assert release.wait(1.0)
        return ["billing"]

    classifier.classify.side_effect = block_classification

    assert worker.start() is True
    first_thread = worker.thread
    assert first_thread is not None and first_thread.daemon is True
    assert entered.wait(1.0)
    assert worker.start() is False
    assert worker.thread is first_thread
    assert worker.stop(timeout=0.01) is False
    assert classifier.classify.call_count == 1

    release.set()
    assert worker.stop(timeout=1.0) is True
    assert classifier.classify.call_count == 1


def test_start_waits_for_the_stopping_generation_to_finish(worker, classifier, job_store):
    """A new thread generation must not begin while stop is still draining the old one."""
    entered = threading.Event()
    release = threading.Event()
    stopped = threading.Event()
    job_store.claim.side_effect = [claimed_job(), None]

    def block_classification(*_args):
        entered.set()
        assert release.wait(1.0)
        return ["billing"]

    classifier.classify.side_effect = block_classification
    assert worker.start() is True
    assert entered.wait(1.0)

    result = {}

    def stop_worker():
        result["stopped"] = worker.stop(timeout=1.0)
        stopped.set()

    stopper = threading.Thread(target=stop_worker)
    stopper.start()
    assert worker._stopping.wait(1.0)
    assert worker.start() is False

    release.set()
    assert stopped.wait(1.0)
    stopper.join(1.0)
    assert result["stopped"] is True
    assert worker.start() is True
    assert worker.stop(timeout=1.0) is True


def test_start_remains_blocked_until_the_active_stop_call_returns(worker, classifier, job_store):
    """Thread cleanup alone must not clear a latch owned by a stop call still joining that generation."""
    entered = threading.Event()
    release = threading.Event()
    join_finished = threading.Event()
    release_stop = threading.Event()
    job_store.claim.side_effect = [claimed_job(), None]

    def block_classification(*_args):
        entered.set()
        assert release.wait(1.0)
        return ["billing"]

    classifier.classify.side_effect = block_classification
    assert worker.start() is True
    thread = worker.thread
    assert thread is not None
    original_join = thread.join

    def pause_after_join(timeout=None):
        original_join(timeout)
        join_finished.set()
        assert release_stop.wait(1.0)

    thread.join = pause_after_join
    stopper = threading.Thread(target=lambda: worker.stop(timeout=1.0))
    stopper.start()
    try:
        assert worker._stopping.wait(1.0)
        release.set()
        assert join_finished.wait(1.0)
        assert worker.start() is False

        release_stop.set()
        stopper.join(1.0)
        assert worker.start() is True
        assert worker.stop(timeout=1.0) is True
    finally:
        release.set()
        release_stop.set()
        stopper.join(1.0)
        worker.stop(timeout=1.0)


def test_shutdown_latch_rechecks_after_waiting_for_process_synchronization(worker, job_store):
    """A poller already queued on the process lock must not claim once shutdown has latched."""
    entered = threading.Event()
    release = threading.Event()

    class PausingLock:
        def __init__(self):
            self._lock = threading.Lock()

        def __enter__(self):
            entered.set()
            assert release.wait(1.0)
            self._lock.acquire()
            return self

        def __exit__(self, *_args):
            self._lock.release()

    worker._process_lock = PausingLock()
    assert worker.start() is True
    assert entered.wait(1.0)
    assert worker.stop(timeout=0.01) is False

    release.set()
    assert worker.stop(timeout=1.0) is True
    job_store.claim.assert_not_called()


def test_shutdown_latch_wins_before_the_claim_boundary(worker, job_store):
    """A stop that reaches the claim gate first must prevent a poller from calling the store at all."""
    reached_claim_gate = threading.Event()
    release_poller = threading.Event()
    stopped = threading.Event()
    job_store.claim.return_value = None

    class PausingClaimLock:
        def __init__(self):
            self._lock = threading.Lock()
            self._entries = 0

        def __enter__(self):
            self._entries += 1
            if self._entries == 1:
                reached_claim_gate.set()
                assert release_poller.wait(1.0)
            self._lock.acquire()
            return self

        def __exit__(self, *_args):
            self._lock.release()

    worker._claim_lock = PausingClaimLock()
    assert worker.start() is True
    try:
        assert reached_claim_gate.wait(1.0)
        stopper = threading.Thread(target=lambda: (worker.stop(timeout=1.0), stopped.set()))
        stopper.start()
        assert worker._stopping.wait(1.0)
        release_poller.set()
        assert stopped.wait(1.0)
        stopper.join(1.0)
        job_store.claim.assert_not_called()
    finally:
        release_poller.set()
        worker.stop(timeout=1.0)


def test_claim_entered_before_shutdown_is_in_flight_and_stop_remains_truthful(worker, classifier, job_store):
    """Once the store claim begins, stop waits only for the later blocked provider work and reports timeout."""
    claim_entered = threading.Event()
    classification_entered = threading.Event()
    release = threading.Event()

    def claim(*_args):
        claim_entered.set()
        return claimed_job()

    def block_classification(*_args):
        classification_entered.set()
        assert release.wait(1.0)
        return ["billing"]

    job_store.claim.side_effect = claim
    classifier.classify.side_effect = block_classification
    assert worker.start() is True
    assert claim_entered.wait(1.0)
    assert classification_entered.wait(1.0)
    assert worker.stop(timeout=0.01) is False
    assert worker._stopping.is_set()

    release.set()
    assert worker.stop(timeout=1.0) is True


def test_expired_processing_job_is_claimable_through_the_job_store(session_factory):
    """Restart recovery depends on the durable store converting an expired lease into a new claim."""
    job = CategoryJob(
        id=JOB_ID,
        memory_id="mem-1",
        owner_id=OWNER_ID,
        memory_hash="h1",
        catalog_snapshot=[{"name": "billing", "description": "Invoices and payments."}],
        state=CategoryJobState.PROCESSING,
        worker_id="dead-worker",
        attempts=1,
        lease_expires_at=NOW - timedelta(seconds=1),
        created_at=NOW - timedelta(minutes=1),
    )
    result = MagicMock(name="claim_result")
    result.scalars.return_value.all.return_value = [job]
    result.scalar_one_or_none.return_value = job
    session_factory.return_value.execute.return_value = result
    store = CategoryJobStore(session_factory)
    store.try_memory_fence = MagicMock(return_value=nullcontext(session_factory.return_value))

    claimed = store.claim("new-worker", NOW, 60)

    assert claimed == claimed_job(attempts=2)
    assert job.worker_id == "new-worker"
    assert job.lease_expires_at == NOW + timedelta(seconds=60)


def test_runtime_is_idempotent_and_disabled_workers_never_start(monkeypatch, session_factory):
    """Router dependencies need stable process-wide objects while disabled deployments create no thread."""
    monkeypatch.setattr(category_runtime, "_service", None)
    monkeypatch.setattr(category_runtime, "_worker", None)
    monkeypatch.setenv("CATEGORY_WORKER_ENABLED", "false")
    memory_factory = MagicMock(name="memory_factory")

    service = category_runtime.initialize_category_runtime(
        session_factory=session_factory,
        memory_factory=memory_factory,
    )
    second = category_runtime.initialize_category_runtime(
        session_factory=MagicMock(name="other_session_factory"),
        memory_factory=MagicMock(name="other_memory_factory"),
    )

    assert second is service
    assert category_runtime.get_category_service() is service
    worker = category_runtime.get_category_worker()
    assert worker.enabled is False
    assert worker.thread is None


def test_runtime_worker_peek_never_initializes(monkeypatch):
    monkeypatch.setattr(category_runtime, "_service", None)
    monkeypatch.setattr(category_runtime, "_worker", None)
    initialize = MagicMock(name="initialize_category_runtime")
    monkeypatch.setattr(category_runtime, "initialize_category_runtime", initialize)

    assert category_runtime.get_initialized_category_worker() is None
    initialize.assert_not_called()


def test_runtime_parses_default_and_override_max_attempts(monkeypatch, session_factory):
    """Operators need a validated retry cap rather than an ignored worker setting."""
    monkeypatch.setattr(category_runtime, "_service", None)
    monkeypatch.setattr(category_runtime, "_worker", None)
    default = category_runtime.initialize_category_runtime(
        session_factory=session_factory,
        memory_factory=MagicMock(),
        environment={"CATEGORY_WORKER_ENABLED": "false"},
    )
    assert category_runtime.get_category_worker()._max_attempts == 3

    monkeypatch.setattr(category_runtime, "_service", None)
    monkeypatch.setattr(category_runtime, "_worker", None)
    override = category_runtime.initialize_category_runtime(
        session_factory=session_factory,
        memory_factory=MagicMock(),
        environment={"CATEGORY_WORKER_ENABLED": "false", "CATEGORY_WORKER_MAX_ATTEMPTS": "5"},
    )
    assert override is not default
    assert category_runtime.get_category_worker()._max_attempts == 5


def test_runtime_reconciles_orphaned_pending_payloads_before_worker_start(monkeypatch, session_factory):
    """Startup ordering closes the durable mark-before-enqueue process-crash window."""
    monkeypatch.setattr(category_runtime, "_service", None)
    monkeypatch.setattr(category_runtime, "_worker", None)
    events = []
    monkeypatch.setattr(category_runtime.CategoryService, "reconcile_pending", lambda _service: events.append("reconcile"))
    monkeypatch.setattr(category_runtime.CategoryWorker, "start", lambda _worker: events.append("start"))

    category_runtime.initialize_category_runtime(
        session_factory=session_factory,
        memory_factory=MagicMock(),
        environment={"CATEGORY_WORKER_ENABLED": "true"},
    )

    assert events == ["reconcile", "start"]


def test_runtime_still_starts_when_restart_reconciliation_store_scan_fails(
    monkeypatch, session_factory, caplog
):
    """A transient scan outage must not suppress the worker's ordinary lease recovery path."""
    monkeypatch.setattr(category_runtime, "_service", None)
    monkeypatch.setattr(category_runtime, "_worker", None)
    start = MagicMock(name="start")
    monkeypatch.setattr(
        category_runtime.CategoryService,
        "reconcile_pending",
        MagicMock(side_effect=RuntimeError("secret database detail")),
    )
    monkeypatch.setattr(category_runtime.CategoryWorker, "start", start)

    category_runtime.initialize_category_runtime(
        session_factory=session_factory,
        memory_factory=MagicMock(),
        environment={"CATEGORY_WORKER_ENABLED": "true"},
    )

    start.assert_called_once_with()
    assert "category_restart_reconcile_failed" in caplog.text
    assert "secret database detail" not in caplog.text


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("CATEGORY_WORKER_POLL_SECONDS", "0"),
        ("CATEGORY_WORKER_LEASE_SECONDS", "-1"),
        ("CATEGORY_WORKER_MAX_ATTEMPTS", "0"),
    ],
)
def test_runtime_rejects_non_positive_worker_settings(monkeypatch, session_factory, name, value):
    """Invalid timing settings must fail before a worker can silently busy-loop or lose leases."""
    monkeypatch.setattr(category_runtime, "_service", None)
    monkeypatch.setattr(category_runtime, "_worker", None)
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=name):
        category_runtime.initialize_category_runtime(session_factory=session_factory, memory_factory=MagicMock())
