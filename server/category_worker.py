"""Single-threaded leased processing for durable category-classification jobs."""

import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Callable

from category_classifier import CategoryClassifier, CategoryResultError
from category_models import CategoryJobState
from category_store import CategoryJobStore, ClaimedCategoryJob, MemoryCategoryStore


_MAX_ATTEMPTS = 3
_MAX_BACKOFF_SECONDS = 60
_GENERIC_ERROR_CODE = "category_error"
_GENERIC_ERROR_MESSAGE = "Category classification failed"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CategoryWorker:
    """Claim and process at most one durable category job at a time."""

    def __init__(
        self,
        job_store: CategoryJobStore,
        memory_store: MemoryCategoryStore,
        classifier: CategoryClassifier,
        *,
        worker_id: str | None = None,
        enabled: bool = True,
        poll_seconds: float = 1.0,
        lease_seconds: int = 60,
        max_attempts: int = _MAX_ATTEMPTS,
        now: Callable[[], datetime] = _utcnow,
    ):
        self._job_store = job_store
        self._memory_store = memory_store
        self._classifier = classifier
        self._enabled = enabled
        self._poll_seconds = poll_seconds
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts
        self._now = now
        self.worker_id = worker_id or f"category-worker-{uuid.uuid4()}"
        self._lifecycle_lock = threading.RLock()
        self._process_lock = threading.Lock()
        self._claim_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None
        self._generation = 0
        self._stop_calls = 0

    @property
    def enabled(self) -> bool:
        """Expose whether this deployment permits the background thread to start."""
        return self._enabled

    @property
    def thread(self) -> threading.Thread | None:
        """Expose the current daemon thread for lifecycle observability and tests."""
        with self._lifecycle_lock:
            return self._thread

    def start(self) -> bool:
        """Start one daemon thread, returning true only when this call starts it."""
        with self._lifecycle_lock:
            if not self._enabled:
                return False
            if self._stopping.is_set():
                return False
            if self._thread is not None and self._thread.is_alive():
                return False
            self._stop_event = threading.Event()
            self._generation += 1
            generation = self._generation
            thread = threading.Thread(
                target=self._run,
                args=(self._stop_event, generation),
                daemon=True,
                name="category-worker",
            )
            self._thread = thread
            thread.start()
            return True

    def stop(self, timeout: float = 5.0) -> bool:
        """Request a prompt shutdown and truthfully report whether it completed in time."""
        with self._claim_lock:
            with self._lifecycle_lock:
                self._stopping.set()
                self._stop_calls += 1
                thread = self._thread
                self._stop_event.set()
        if thread is None:
            with self._lifecycle_lock:
                self._finish_stop_call()
            return True
        thread.join(timeout)
        stopped = not thread.is_alive()
        with self._lifecycle_lock:
            if stopped:
                if self._thread is thread:
                    self._thread = None
            self._finish_stop_call()
        return stopped

    def process_once(self) -> bool:
        """Claim and process one job while ensuring classification has only one in-flight call."""
        with self._process_lock:
            if self._stop_event.is_set() or self._stopping.is_set():
                return False
            return self._process_once()

    def _process_once(self) -> bool:
        try:
            with self._claim_lock:
                if self._stop_event.is_set() or self._stopping.is_set():
                    return False
                job = self._job_store.claim(self.worker_id, self._now(), self._lease_seconds)
        except Exception:
            logging.warning("category_worker_claim_failed worker_id=%s error_code=%s", self.worker_id, _GENERIC_ERROR_CODE)
            return False
        if job is None:
            return False

        try:
            with self._job_store.memory_fence(job.memory_id) as session:
                memory = self._memory_store.get(job.memory_id)
                if memory is None:
                    self._call_job(
                        self._job_store.cancel,
                        job.id,
                        self.worker_id,
                        "memory_deleted",
                        owner_id=job.owner_id,
                        now=self._now(),
                        session=session,
                        lease_fenced=True,
                    )
                    return True
                if memory.user_id != job.owner_id:
                    self._call_job(
                        self._job_store.cancel,
                        job.id,
                        self.worker_id,
                        "owner_mismatch",
                        owner_id=job.owner_id,
                        now=self._now(),
                        session=session,
                        lease_fenced=True,
                    )
                    return True
                if memory.memory_hash != job.memory_hash:
                    self._call_job(
                        self._job_store.cancel,
                        job.id,
                        self.worker_id,
                        "replaced",
                        owner_id=job.owner_id,
                        now=self._now(),
                        session=session,
                        lease_fenced=True,
                    )
                    return True
                generation = str(job.id)
                if memory.category_generation != generation:
                    if memory.category_generation is not None:
                        self._call_job(
                            self._job_store.cancel,
                            job.id,
                            self.worker_id,
                            "replaced",
                            owner_id=job.owner_id,
                            now=self._now(),
                            session=session,
                        )
                        return True
                    memory = self._memory_store.mark_pending(
                        job.memory_id,
                        generation,
                        owner_id=job.owner_id,
                        expected_hash=job.memory_hash,
                        expected_text=memory.text,
                        expected_generation=None,
                        expected_origin=None,
                    )
                    if memory is None:
                        self._call_job(
                            self._job_store.cancel,
                            job.id,
                            self.worker_id,
                            "replaced",
                            owner_id=job.owner_id,
                            now=self._now(),
                            session=session,
                        )
                        return True
                if not self._call_job(
                    self._job_store.renew,
                    job.id,
                    self.worker_id,
                    owner_id=job.owner_id,
                    now=self._now(),
                    lease_seconds=self._lease_seconds,
                    session=session,
                ):
                    return True
        except CategoryResultError as error:
            self._reschedule_or_fail(job, error.code, error.safe_message, self._now())
            return True
        except Exception:
            logging.warning(
                "category_worker_job_failed job_id=%s memory_id=%s error_code=%s",
                job.id,
                job.memory_id,
                _GENERIC_ERROR_CODE,
            )
            self._reschedule_or_fail(job, _GENERIC_ERROR_CODE, _GENERIC_ERROR_MESSAGE, self._now())
            return True

        if job.terminalizing:
            self._finalize_failed(
                job,
                job.terminal_error_code or _GENERIC_ERROR_CODE,
                job.terminal_error_message or _GENERIC_ERROR_MESSAGE,
            )
            return True

        try:
            categories, lease_live = self._classify_with_heartbeat(job, memory.text)
            if not lease_live:
                return True
            with self._job_store.memory_fence(job.memory_id) as session:
                if not self._call_job(
                    self._job_store.renew,
                    job.id,
                    self.worker_id,
                    owner_id=job.owner_id,
                    now=self._now(),
                    lease_seconds=self._lease_seconds,
                    session=session,
                ):
                    return True
                if self._memory_store.write_result(
                    job.memory_id,
                    job.memory_hash,
                    generation,
                    categories,
                    "completed",
                    owner_id=job.owner_id,
                ):
                    self._call_job(
                        self._job_store.complete,
                        job.id,
                        self.worker_id,
                        owner_id=job.owner_id,
                        now=self._now(),
                        session=session,
                        lease_fenced=True,
                    )
                else:
                    self._call_job(
                        self._job_store.cancel,
                        job.id,
                        self.worker_id,
                        "replaced",
                        owner_id=job.owner_id,
                        now=self._now(),
                        session=session,
                        lease_fenced=True,
                    )
        except CategoryResultError as error:
            self._reschedule_or_fail(job, error.code, error.safe_message, self._now())
        except Exception:
            logging.warning(
                "category_worker_job_failed job_id=%s memory_id=%s error_code=%s",
                job.id,
                job.memory_id,
                _GENERIC_ERROR_CODE,
            )
            self._reschedule_or_fail(job, _GENERIC_ERROR_CODE, _GENERIC_ERROR_MESSAGE, self._now())
        return True

    def _classify_with_heartbeat(
        self, job: ClaimedCategoryJob, memory_text: str
    ) -> tuple[list[str], bool]:
        """Renew the lease while the provider call runs without holding the memory fence."""
        stopped = threading.Event()
        lease_lost = threading.Event()
        interval = max(min(self._lease_seconds / 3, 20.0), 0.01)

        def heartbeat() -> None:
            while not stopped.wait(interval):
                try:
                    if not self._job_store.renew(
                        job.id,
                        self.worker_id,
                        owner_id=job.owner_id,
                        now=self._now(),
                        lease_seconds=self._lease_seconds,
                    ):
                        lease_lost.set()
                        return
                except Exception:
                    lease_lost.set()
                    return

        thread = threading.Thread(target=heartbeat, daemon=True, name="category-lease-heartbeat")
        thread.start()
        try:
            categories = self._classifier.classify(memory_text, job.catalog)
        finally:
            stopped.set()
            thread.join(max(interval * 2, 0.1))
        return categories, not lease_lost.is_set()

    def _reschedule_or_fail(self, job: ClaimedCategoryJob, error_code: str, error_message: str, now: datetime) -> None:
        """Retry safely, terminalizing only after the failed payload write succeeds."""
        try:
            with self._job_store.memory_fence(job.memory_id) as session:
                state = self._call_job(
                    self._job_store.reschedule_or_fail,
                    job.id,
                    self.worker_id,
                    owner_id=job.owner_id,
                    now=now,
                    error_code=error_code,
                    error_message=error_message,
                    max_attempts=self._max_attempts,
                    session=session,
                )
                if state in {CategoryJobState.RETRYING, CategoryJobState.PROCESSING}:
                    logging.warning(
                        "category_worker_job_%s job_id=%s memory_id=%s",
                        "failing" if state == CategoryJobState.PROCESSING else state.value,
                        job.id,
                        job.memory_id,
                    )
                if state == CategoryJobState.PROCESSING:
                    self._finalize_failed_locked(job, error_code, error_message, session)
        except Exception:
            logging.warning(
                "category_worker_retry_failed job_id=%s memory_id=%s error_code=%s",
                job.id,
                job.memory_id,
                _GENERIC_ERROR_CODE,
            )

    def _finalize_failed(
        self,
        job: ClaimedCategoryJob,
        error_code: str,
        error_message: str,
    ) -> None:
        try:
            with self._job_store.memory_fence(job.memory_id) as session:
                self._finalize_failed_locked(job, error_code, error_message, session)
        except Exception:
            logging.warning(
                "category_worker_retry_failed job_id=%s memory_id=%s error_code=%s",
                job.id,
                job.memory_id,
                _GENERIC_ERROR_CODE,
            )

    def _finalize_failed_locked(
        self,
        job: ClaimedCategoryJob,
        error_code: str,
        error_message: str,
        session: object | None,
    ) -> None:
        try:
            written = self._memory_store.write_result(
                job.memory_id,
                job.memory_hash,
                str(job.id),
                [],
                "failed",
                owner_id=job.owner_id,
            )
        except Exception:
            self._call_job(
                self._job_store.reschedule_terminalization,
                job.id,
                self.worker_id,
                owner_id=job.owner_id,
                now=self._now(),
                max_backoff_seconds=_MAX_BACKOFF_SECONDS,
                session=session,
                lease_fenced=True,
            )
            return
        if written:
            self._call_job(
                self._job_store.fail,
                job.id,
                self.worker_id,
                owner_id=job.owner_id,
                now=self._now(),
                error_code=error_code,
                error_message=error_message,
                session=session,
                lease_fenced=True,
            )
        else:
            self._call_job(
                self._job_store.cancel,
                job.id,
                self.worker_id,
                "replaced",
                owner_id=job.owner_id,
                now=self._now(),
                session=session,
                lease_fenced=True,
            )

    @staticmethod
    def _call_job(method: Callable, *args, session: object | None, **kwargs):
        if session is not None:
            kwargs["session"] = session
        return method(*args, **kwargs)

    def _run(self, stop_event: threading.Event, generation: int) -> None:
        """Poll with an interruptible event so shutdown does not wait for idle sleeps."""
        try:
            while not stop_event.is_set():
                if not self.process_once():
                    stop_event.wait(self._poll_seconds)
        finally:
            with self._lifecycle_lock:
                if self._generation == generation and self._thread is threading.current_thread():
                    self._thread = None
                    if self._stop_calls == 0:
                        self._stopping.clear()

    def _finish_stop_call(self) -> None:
        """Release the generation latch only after every joining stop call has returned."""
        self._stop_calls -= 1
        if self._stop_calls == 0 and self._thread is None:
            self._stopping.clear()
