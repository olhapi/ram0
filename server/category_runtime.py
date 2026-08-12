"""Locked process-wide construction for category services and their worker."""

import logging
import math
import os
import threading
from collections.abc import Callable, Mapping

from category_classifier import CategoryClassifier
from category_service import CategoryService
from category_store import CategoryCatalogStore, CategoryJobStore, MemoryCategoryStore
from category_worker import CategoryWorker
from db import SessionLocal
from memory_owner_migration import require_ownership_ready
from server_state import get_memory_instance


_runtime_lock = threading.RLock()
_service: CategoryService | None = None
_worker: CategoryWorker | None = None


def _enabled_from(environment: Mapping[str, str]) -> bool:
    value = environment.get("CATEGORY_WORKER_ENABLED", "true").strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError("CATEGORY_WORKER_ENABLED must be a boolean value.")


def _positive_float(environment: Mapping[str, str], name: str, default: float) -> float:
    raw_value = environment.get(name, str(default))
    try:
        value = float(raw_value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a positive number.") from error
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive number.")
    return value


def _positive_int(environment: Mapping[str, str], name: str, default: int) -> int:
    raw_value = environment.get(name, str(default))
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a positive integer.") from error
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer.")
    return value


def initialize_category_runtime(
    *,
    session_factory: Callable = SessionLocal,
    memory_factory: Callable[[], object] = get_memory_instance,
    environment: Mapping[str, str] | None = None,
) -> CategoryService:
    """Create one service and worker pair, starting it only when configuration enables it."""
    global _service, _worker
    with _runtime_lock:
        if _service is not None and _worker is not None:
            _worker.start()
            return _service

        settings = os.environ if environment is None else environment
        enabled = _enabled_from(settings)
        poll_seconds = _positive_float(settings, "CATEGORY_WORKER_POLL_SECONDS", 1.0)
        lease_seconds = _positive_int(settings, "CATEGORY_WORKER_LEASE_SECONDS", 60)
        max_attempts = _positive_int(settings, "CATEGORY_WORKER_MAX_ATTEMPTS", 3)
        catalog_store = CategoryCatalogStore(session_factory)
        job_store = CategoryJobStore(session_factory)
        memory_store = MemoryCategoryStore(memory_factory)
        classifier = CategoryClassifier(memory_factory)
        _service = CategoryService(catalog_store, job_store, memory_store, classifier)
        _worker = CategoryWorker(
            job_store,
            memory_store,
            classifier,
            enabled=enabled,
            poll_seconds=poll_seconds,
            lease_seconds=lease_seconds,
            max_attempts=max_attempts,
        )
        if enabled:
            try:
                _service.reconcile_pending()
            except Exception:
                logging.warning("category_restart_reconcile_failed error_code=store_unavailable")
        _worker.start()
        return _service


def get_category_service() -> CategoryService:
    """Return the lazily initialized service without importing routers or the FastAPI app."""
    with _runtime_lock:
        if _service is not None and _worker is not None:
            return _service
    require_ownership_ready()
    return initialize_category_runtime()


def get_category_worker() -> CategoryWorker:
    """Return the worker paired with the lazily initialized category service."""
    with _runtime_lock:
        if _service is None or _worker is None:
            initialize_category_runtime()
        if _worker is None:
            raise RuntimeError("Category runtime was not initialized.")
        return _worker


def get_initialized_category_worker() -> CategoryWorker | None:
    """Return the existing worker without creating or starting category runtime."""
    with _runtime_lock:
        return _worker
