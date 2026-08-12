"""Category catalog, ingestion lifecycle, and explicit reclassification orchestration."""

import logging
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict

from category_classifier import CategoryClassifier
from category_models import CategoryDefinition, CategoryJobState, EffectiveCatalog, default_catalog, promote_category_fields, validate_catalog
from category_store import CategoryCatalogStore, CategoryJobStore, EnqueueResult, MemoryCategoryStore, MemorySnapshot
from models import CategoryJob


_RECLASSIFICATION_SCOPES = frozenset(("unclassified_failed", "all"))
_ORIGIN_JOB_NAMESPACE = uuid.UUID("86f317ef-460a-4e20-b9c8-f3394cebd1d0")
_UPDATE_TEXT_UNSET = object()
_INSTALL_ATTEMPTS = 3


class CatalogView(BaseModel):
    """The persisted catalog, currently active catalog, and durable memory-label counts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    saved: tuple[CategoryDefinition, ...]
    active: tuple[CategoryDefinition, ...]
    source: Literal["defaults", "project"]
    counts: dict[str, int]


class ReclassificationPreview(BaseModel):
    """A dry-run result that never queues work or calls an LLM."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: Literal["unclassified_failed", "all"]
    eligible_memories: int
    estimated_calls: int
    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_cost: float | None


class ReclassificationStart(BaseModel):
    """The idempotent result of starting durable historical classification jobs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    created_jobs: int
    skipped_active_jobs: int
    eligible_memories: int


@dataclass(frozen=True, slots=True)
class CategoryUpdateOutcome:
    """Core update result plus the best-effort category reconciliation outcome."""

    response: object
    category_processing_failed: bool


class CategoryService:
    """Own the category behavior that surrounds otherwise unchanged memory operations."""

    def __init__(
        self,
        catalog_store: CategoryCatalogStore,
        job_store: CategoryJobStore,
        memory_store: MemoryCategoryStore,
        classifier: CategoryClassifier,
    ):
        self._catalog_store = catalog_store
        self._job_store = job_store
        self._memory_store = memory_store
        self._classifier = classifier

    def get_catalog_view(self, owner_id: str) -> CatalogView:
        """Expose saved versus effective labels while retaining counts for retired labels."""
        saved = self._catalog_store.get_saved(owner_id)
        effective = self._effective_catalog(saved)
        return CatalogView(
            saved=saved,
            active=effective.definitions,
            source=effective.source,
            counts=self._memory_store.category_counts(owner_id),
        )

    def create_category(self, definition: CategoryDefinition, owner_id: str) -> CatalogView:
        """Append one validated project category without initiating a historical backfill."""
        updated = self._catalog_store.create(owner_id, definition)
        return self._catalog_view_for_saved(updated, owner_id)

    def replace_catalog(
        self, definitions: tuple[CategoryDefinition, ...] | list[CategoryDefinition], owner_id: str
    ) -> CatalogView:
        """Atomically replace the saved catalog; an empty catalog deliberately restores defaults."""
        saved = validate_catalog(definitions)
        self._catalog_store.replace(owner_id, saved)
        return self._catalog_view_for_saved(saved, owner_id)

    def update_category(
        self,
        name: str,
        owner_id: str,
        *,
        new_name: str | None = None,
        description: str | None = None,
    ) -> CatalogView:
        """Patch one definition against the catalog loaded under the store transaction."""
        updated = self._catalog_store.update(owner_id, name, new_name=new_name, description=description)
        return self._catalog_view_for_saved(updated, owner_id)

    def delete_category(self, name: str, owner_id: str) -> CatalogView:
        """Remove one saved definition without relabeling memories that already use it."""
        updated = self._catalog_store.delete(owner_id, name)
        return self._catalog_view_for_saved(updated, owner_id)

    def list_jobs(
        self, *, owner_id: str, states: tuple[CategoryJobState, ...] | None = None, limit: int = 100
    ) -> list[CategoryJob]:
        """List durable jobs without leaking the store implementation to route handlers."""
        return self._job_store.list_jobs(owner_id=uuid.UUID(owner_id), states=states, limit=limit)

    @contextmanager
    def owner_fence(self, owner_id: str):
        """Serialize one owner's add/reset lifecycle before any per-memory fence is acquired."""
        with self._job_store.owner_fence(uuid.UUID(owner_id)):
            yield

    def resolve_catalog(
        self,
        owner_id: str,
        request_catalog: tuple[CategoryDefinition, ...] | list[CategoryDefinition] | None,
        *,
        session: object | None = None,
    ) -> EffectiveCatalog:
        """Select the strict request, project, then default catalog precedence order."""
        if request_catalog is not None:
            catalog = validate_catalog(request_catalog)
            if not catalog:
                raise ValueError("Per-call categories must not be empty.")
            return EffectiveCatalog(definitions=catalog, source="request")
        return self._effective_catalog(
            self._call_job(self._catalog_store.get_saved, owner_id, session=session)
        )

    def enqueue_memory(
        self,
        memory_id: str,
        catalog: EffectiveCatalog,
        *,
        replace_active: bool = False,
        expected_text: str | None = None,
        expected_origin: str | None = None,
    ) -> bool:
        """Mark a memory pending and enqueue an immutable classification snapshot without blocking callers."""
        return (
            self._enqueue_snapshot(
                memory_id,
                catalog,
                replace_active=replace_active,
                expected_text=expected_text,
                expected_origin=expected_origin,
            )
            is not None
        )

    def after_add(
        self,
        response: object,
        catalog: EffectiveCatalog,
        *,
        origin_token: str | None = None,
    ) -> object:
        """Best-effort queue ADD and UPDATE result memories while preserving the core response shape."""
        result = promote_category_fields(response)
        if not isinstance(result, dict) or not isinstance(result.get("results"), list):
            return result

        for memory in result["results"]:
            if not isinstance(memory, dict) or memory.get("event") not in {"ADD", "UPDATE"}:
                continue
            memory_id = memory.get("id")
            origin_text = memory.get("memory")
            if not isinstance(memory_id, str) or not isinstance(origin_text, str):
                continue
            failure_snapshot: MemorySnapshot | None = None
            try:
                with self._job_store.memory_fence(memory_id) as session:
                    current = self._memory_store.get(memory_id)
                    if (
                        current is None
                        or current.text != origin_text
                        or (origin_token is not None and current.category_origin != origin_token)
                    ):
                        continue
                    failure_snapshot = current
                    if origin_token is None and (
                        current.category_status == "pending"
                        and current.category_generation is not None
                        and self._call_job(
                            self._job_store.active_matches,
                            memory_id,
                            current.memory_hash,
                            current.category_generation,
                            owner_id=current.user_id,
                            session=session,
                        )
                    ):
                        queued = True
                        memory["categories"] = None
                        memory["category_status"] = "pending"
                        continue
                    queued = self._enqueue_snapshot_locked(
                        memory_id,
                        catalog,
                        replace_active=True,
                        expected_text=origin_text,
                        expected_origin=origin_token,
                        session=session,
                    ) is not None
                    if not queued:
                        self._persist_failed(failure_snapshot)
            except Exception:
                logging.warning("category_after_add_failed memory_id=%s error_code=enqueue_failed", memory_id)
                if failure_snapshot is not None:
                    self._persist_failed(failure_snapshot)
                queued = False
            memory["categories"] = None if queued else []
            memory["category_status"] = "pending" if queued else "failed"
        return result

    def after_text_update(self, memory_id: str, *, owner_id: str) -> bool:
        """Replace active work after a text change so the job holds the newest memory hash and catalog."""
        return self.after_update(memory_id, owner_id=owner_id, text_changed=True)

    def after_update(self, memory_id: str, *, owner_id: str, text_changed: bool) -> bool:
        """Reconcile the current post-update payload with its active generation."""
        with self._job_store.memory_fence(memory_id) as session:
            return self._after_update_locked(memory_id, owner_id=owner_id, text_changed=text_changed, session=session)

    def run_memory_update(
        self,
        memory_id: str,
        operation: Callable[[], object],
        *,
        owner_id: str,
        supplied_text: object = _UPDATE_TEXT_UNSET,
        with_category_outcome: bool = False,
    ) -> object:
        """Fence the core whole-payload update and post-write reconciliation as one operation."""
        with self._job_store.memory_fence(memory_id) as session:
            previous = self._memory_store.get(memory_id)
            response = operation()
            text_changed = bool(
                supplied_text is not _UPDATE_TEXT_UNSET
                and (previous is None or previous.text != supplied_text)
            )
            category_processing_failed = False
            try:
                category_processing_failed = not self._after_update_locked(
                    memory_id, owner_id=owner_id, text_changed=text_changed, session=session
                )
            except Exception:
                category_processing_failed = True
                logging.warning(
                    "category_after_text_update_failed memory_id=%s error_code=enqueue_failed",
                    memory_id,
                )
            if with_category_outcome:
                return CategoryUpdateOutcome(
                    response=response,
                    category_processing_failed=category_processing_failed,
                )
            return response

    def _after_update_locked(
        self, memory_id: str, *, owner_id: str, text_changed: bool, session: object | None = None
    ) -> bool:
        """Reconcile after the caller has acquired the cross-process memory fence."""
        snapshot = self._memory_store.get(memory_id)
        if snapshot is None or str(snapshot.user_id) != str(uuid.UUID(owner_id)):
            return False
        if not text_changed:
            if snapshot.category_status != "pending":
                return True
            if snapshot.category_generation is not None and self._call_job(
                self._job_store.active_matches,
                memory_id,
                snapshot.memory_hash,
                snapshot.category_generation,
                owner_id=snapshot.user_id,
                session=session,
            ):
                return True
        try:
            queued = self._enqueue_snapshot_locked(
                memory_id,
                self.resolve_catalog(owner_id, None, session=session),
                replace_active=True,
                expected_text=snapshot.text,
                session=session,
            ) is not None
        except Exception:
            self._persist_failed(snapshot)
            raise
        if not queued:
            self._persist_failed(snapshot)
        return queued

    def reconcile_pending(self) -> int:
        """Repair every durable preparation, then any remaining vector-only marker."""
        repaired = 0
        catalogs: dict[uuid.UUID, EffectiveCatalog] = {}
        for prepared in self._job_store.list_prepared():
            try:
                with self._job_store.memory_fence(prepared.memory_id) as session:
                    current = self._memory_store.get(prepared.memory_id)
                    latest = self._call_job(
                        self._job_store.preparation_is_latest,
                        prepared.id,
                        prepared.memory_id,
                        prepared.owner_id,
                        session=session,
                    )
                    foreign_generation = bool(
                        current
                        and current.category_generation is not None
                        and current.category_generation != str(prepared.id)
                    )
                    foreign_origin = bool(
                        current
                        and current.category_origin is not None
                        and self._generation_for_origin(
                            current.category_origin, current.memory_id
                        )
                        != prepared.id
                    )
                    if (
                        not latest
                        or current is None
                        or current.user_id != prepared.owner_id
                        or current.memory_hash != prepared.memory_hash
                        or foreign_generation
                        or foreign_origin
                    ):
                        self._call_job(
                            self._job_store.cancel_prepared,
                            prepared.id,
                            prepared.owner_id,
                            session=session,
                        )
                        continue
                    marked = self._memory_store.mark_pending(
                        current.memory_id,
                        str(prepared.id),
                        owner_id=prepared.owner_id,
                        expected_hash=current.memory_hash,
                        expected_text=current.text,
                        expected_generation=current.category_generation,
                        expected_origin=current.category_origin,
                    )
                    if marked is None:
                        continue
                    if self._install_prepared(prepared.id, prepared.owner_id, session=session):
                        repaired += 1
                    else:
                        self._terminalize_install_failure(marked, prepared.id, session=session)
            except Exception:
                logging.warning(
                    "category_reconcile_failed memory_id=%s error_code=enqueue_failed",
                    prepared.memory_id,
                )

        for observed in self._memory_store.iter_all_snapshots():
            if observed.category_status != "pending" and observed.category_origin is None:
                continue
            try:
                with self._job_store.memory_fence(observed.memory_id) as session:
                    snapshot = self._memory_store.get(observed.memory_id)
                    if snapshot is None:
                        continue
                    if snapshot.category_origin is not None:
                        generation = self._generation_for_origin(
                            snapshot.category_origin, snapshot.memory_id
                        )
                        if self._call_job(
                            self._job_store.prepared_matches,
                            snapshot.memory_id,
                            snapshot.memory_hash,
                            str(generation),
                            owner_id=snapshot.user_id,
                            session=session,
                        ):
                            marked = self._memory_store.mark_pending(
                                snapshot.memory_id,
                                str(generation),
                                owner_id=snapshot.user_id,
                                expected_hash=snapshot.memory_hash,
                                expected_text=snapshot.text,
                                expected_generation=snapshot.category_generation,
                                expected_origin=snapshot.category_origin,
                            )
                            if marked is not None and self._install_prepared(
                                generation, snapshot.user_id, session=session
                            ):
                                repaired += 1
                            elif marked is not None:
                                self._terminalize_install_failure(marked, generation, session=session)
                            continue
                        catalog = catalogs.get(snapshot.user_id)
                        if catalog is None:
                            catalog = self.resolve_catalog(str(snapshot.user_id), None, session=session)
                            catalogs[snapshot.user_id] = catalog
                        if self._enqueue_snapshot_locked(
                            snapshot.memory_id,
                            catalog,
                            replace_active=True,
                            expected_text=snapshot.text,
                            expected_origin=snapshot.category_origin,
                            session=session,
                        ) is not None:
                            repaired += 1
                            continue
                    if snapshot.category_status != "pending":
                        continue
                    if snapshot.category_generation is not None:
                        if self._call_job(
                            self._job_store.active_matches,
                            snapshot.memory_id,
                            snapshot.memory_hash,
                            snapshot.category_generation,
                            owner_id=snapshot.user_id,
                            session=session,
                        ):
                            continue
                        if self._call_job(
                            self._job_store.prepared_matches,
                            snapshot.memory_id,
                            snapshot.memory_hash,
                            snapshot.category_generation,
                            owner_id=snapshot.user_id,
                            session=session,
                        ):
                            generation = uuid.UUID(snapshot.category_generation)
                            if self._install_prepared(generation, snapshot.user_id, session=session):
                                repaired += 1
                            else:
                                self._terminalize_install_failure(snapshot, generation, session=session)
                            continue
                    catalog = catalogs.get(snapshot.user_id)
                    if catalog is None:
                        catalog = self.resolve_catalog(str(snapshot.user_id), None, session=session)
                        catalogs[snapshot.user_id] = catalog
                    if self._enqueue_snapshot_locked(
                        snapshot.memory_id,
                        catalog,
                        replace_active=True,
                        expected_text=snapshot.text,
                        session=session,
                    ) is not None:
                        repaired += 1
            except Exception:
                logging.warning(
                    "category_reconcile_failed memory_id=%s error_code=enqueue_failed",
                    observed.memory_id,
                )
        return repaired

    def after_delete(self, memory_id: str, owner_id: str) -> bool:
        """Cancel active classification work after a memory has been deleted."""
        try:
            self._job_store.cancel_active(memory_id, uuid.UUID(owner_id))
        except Exception:
            logging.warning("category_after_delete_failed memory_id=%s error_code=cancel_failed", memory_id)
            return False
        return True

    def after_owner_reset(self, owner_id: str) -> bool:
        """Purge every durable category-job row owned by the reset account."""
        try:
            self._job_store.purge_owner(uuid.UUID(owner_id))
        except Exception:
            logging.warning("category_after_owner_reset_failed error_code=cancel_failed")
            return False
        return True

    def preview_reclassification(
        self,
        *,
        scope: Literal["unclassified_failed", "all"],
        owner_id: str,
        input_rate_per_million: float | None = None,
        output_rate_per_million: float | None = None,
    ) -> ReclassificationPreview:
        """Estimate eligible work and optional operator-provided cost without queueing or model calls."""
        checked_scope = self._validate_scope(scope)
        catalog = self.resolve_catalog(owner_id, None)
        eligible = self._eligible_snapshots(checked_scope, owner_id)
        input_tokens = 0
        output_tokens = 0
        for item in eligible:
            input_estimate, output_estimate = self._classifier.estimate_tokens(item.text, catalog.definitions)
            input_tokens += input_estimate
            output_tokens += output_estimate
        cost = None
        if input_rate_per_million is not None and output_rate_per_million is not None:
            cost = (input_tokens * input_rate_per_million + output_tokens * output_rate_per_million) / 1_000_000
        return ReclassificationPreview(
            scope=checked_scope,
            eligible_memories=len(eligible),
            estimated_calls=len(eligible),
            estimated_input_tokens=input_tokens,
            estimated_output_tokens=output_tokens,
            estimated_cost=cost,
        )

    def start_reclassification(
        self, *, scope: Literal["unclassified_failed", "all"], confirm: str, owner_id: str
    ) -> ReclassificationStart:
        """Create only missing active jobs after exact explicit confirmation."""
        if confirm != "RECLASSIFY":
            raise ValueError("Reclassification requires confirm='RECLASSIFY'.")
        checked_scope = self._validate_scope(scope)
        catalog = self.resolve_catalog(owner_id, None)
        eligible = self._eligible_snapshots(checked_scope, owner_id)
        created_jobs = 0
        skipped_active_jobs = 0
        for item in eligible:
            result = self._enqueue_snapshot(
                item.memory_id,
                catalog,
                replace_active=False,
                expected_owner_id=owner_id,
            )
            if result is None:
                continue
            if result.created:
                created_jobs += 1
            else:
                skipped_active_jobs += 1
        return ReclassificationStart(
            created_jobs=created_jobs,
            skipped_active_jobs=skipped_active_jobs,
            eligible_memories=len(eligible),
        )

    def _enqueue_snapshot(
        self,
        memory_id: str,
        catalog: EffectiveCatalog,
        *,
        replace_active: bool,
        expected_owner_id: str | None = None,
        expected_text: str | None = None,
        expected_origin: str | None = None,
    ) -> EnqueueResult | None:
        """Fence the durable prepare, vector CAS, and active-job installation."""
        with self._job_store.memory_fence(memory_id) as session:
            return self._enqueue_snapshot_locked(
                memory_id,
                catalog,
                replace_active=replace_active,
                expected_owner_id=expected_owner_id,
                expected_text=expected_text,
                expected_origin=expected_origin,
                session=session,
            )

    def _enqueue_snapshot_locked(
        self,
        memory_id: str,
        catalog: EffectiveCatalog,
        *,
        replace_active: bool,
        expected_owner_id: str | None = None,
        expected_text: str | None = None,
        expected_origin: str | None = None,
        session: object | None = None,
    ) -> EnqueueResult | None:
        """Install one generation while the caller holds the per-memory database fence."""
        current = self._memory_store.get(memory_id)
        if (
            current is None
            or (expected_owner_id is not None and str(current.user_id) != expected_owner_id)
            or (expected_text is not None and current.text != expected_text)
            or (expected_origin is not None and current.category_origin != expected_origin)
        ):
            logging.warning("category_enqueue_failed memory_id=%s error_code=memory_not_found", memory_id)
            return None
        if (
            not replace_active
            and current.category_generation is not None
            and self._call_job(
                self._job_store.active_matches,
                memory_id,
                current.memory_hash,
                current.category_generation,
                owner_id=current.user_id,
                session=session,
            )
        ):
            return EnqueueResult(job_id=uuid.UUID(current.category_generation), created=False)
        generation = (
            self._generation_for_origin(expected_origin, memory_id)
            if expected_origin is not None
            else uuid.uuid4()
        )
        try:
            reservation = self._call_job(
                self._job_store.prepare,
                memory_id,
                current.memory_hash,
                catalog.definitions,
                job_id=generation,
                owner_id=current.user_id,
                session=session,
            )
            if not self._call_job(
                self._job_store.preparation_is_latest,
                reservation.job_id,
                memory_id,
                current.user_id,
                session=session,
            ):
                self._call_job(
                    self._job_store.cancel_prepared,
                    reservation.job_id,
                    current.user_id,
                    session=session,
                )
                if current.category_origin is not None:
                    if current.category_generation in {None, str(reservation.job_id)}:
                        self._memory_store.fail_origin(current)
                    else:
                        self._memory_store.clear_origin(current)
                logging.warning(
                    "category_enqueue_failed memory_id=%s error_code=memory_not_found",
                    memory_id,
                )
                return None
        except Exception:
            logging.warning("category_enqueue_failed memory_id=%s error_code=enqueue_failed", memory_id)
            return None
        try:
            snapshot = self._memory_store.mark_pending(
                memory_id,
                str(reservation.job_id),
                owner_id=current.user_id,
                expected_hash=current.memory_hash,
                expected_text=current.text,
                expected_generation=current.category_generation,
                expected_origin=current.category_origin,
            )
        except Exception:
            logging.warning("category_enqueue_failed memory_id=%s error_code=enqueue_failed", memory_id)
            return None
        if snapshot is None:
            try:
                self._call_job(
                    self._job_store.cancel_prepared,
                    reservation.job_id,
                    current.user_id,
                    session=session,
                )
            except Exception:
                pass
            logging.warning("category_enqueue_failed memory_id=%s error_code=memory_not_found", memory_id)
            return None

        installed = self._install_prepared(reservation.job_id, current.user_id, session=session)
        if not installed:
            self._terminalize_install_failure(snapshot, reservation.job_id, session=session)
            logging.warning("category_enqueue_failed memory_id=%s error_code=enqueue_failed", memory_id)
            return None
        return reservation

    def _install_prepared(
        self, job_id: uuid.UUID, owner_id: uuid.UUID, *, session: object | None = None
    ) -> bool:
        for _attempt in range(_INSTALL_ATTEMPTS):
            try:
                return self._call_job(
                    self._job_store.install_prepared, job_id, owner_id, session=session
                )
            except Exception:
                continue
        return False

    def _terminalize_install_failure(
        self,
        snapshot: MemorySnapshot,
        job_id: uuid.UUID,
        *,
        session: object | None,
    ) -> bool:
        """Leave a recoverable preparation unless its owned payload can be made terminal."""
        if not self._memory_store.write_result(
            snapshot.memory_id,
            snapshot.memory_hash,
            str(job_id),
            [],
            "failed",
            owner_id=snapshot.user_id,
        ):
            return False
        try:
            self._call_job(
                self._job_store.cancel_prepared,
                job_id,
                snapshot.user_id,
                session=session,
            )
        except Exception:
            return False
        return True

    def _persist_failed(self, snapshot: MemorySnapshot) -> bool:
        """Best-effort exact-snapshot failure state after core memory persistence succeeded."""
        try:
            return self._memory_store.fail_origin(snapshot)
        except Exception:
            return False

    @staticmethod
    def _call_job(method: Callable, *args, session: object | None, **kwargs):
        """Pass the pinned fence session while retaining lightweight test/store adapters."""
        if session is not None:
            kwargs["session"] = session
        return method(*args, **kwargs)

    @staticmethod
    def _generation_for_origin(origin_token: str, memory_id: str) -> uuid.UUID:
        return uuid.uuid5(_ORIGIN_JOB_NAMESPACE, f"{origin_token}:{memory_id}")

    @staticmethod
    def _effective_catalog(saved: tuple[CategoryDefinition, ...]) -> EffectiveCatalog:
        if saved:
            return EffectiveCatalog(definitions=saved, source="project")
        return EffectiveCatalog(definitions=default_catalog(), source="defaults")

    def _catalog_view_for_saved(self, saved: tuple[CategoryDefinition, ...], owner_id: str) -> CatalogView:
        effective = self._effective_catalog(saved)
        return CatalogView(
            saved=saved,
            active=effective.definitions,
            source=effective.source,
            counts=self._memory_store.category_counts(owner_id),
        )

    def _eligible_snapshots(
        self, scope: Literal["unclassified_failed", "all"], owner_id: str
    ) -> tuple[MemorySnapshot, ...]:
        snapshots = tuple(self._memory_store.iter_snapshots(owner_id))
        if scope == "all":
            return snapshots
        return tuple(item for item in snapshots if item.category_status in {"unclassified", "failed"})

    @staticmethod
    def _validate_scope(scope: str) -> Literal["unclassified_failed", "all"]:
        if scope not in _RECLASSIFICATION_SCOPES:
            raise ValueError("Unknown reclassification scope.")
        return scope  # type: ignore[return-value]
