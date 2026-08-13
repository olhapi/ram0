"""Safety contracts for claiming legacy memories during the one-admin upgrade."""

import asyncio
import importlib
import os
import threading
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("fastapi")

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from models import Base, CategoryJob, RefreshTokenJti, Settings, User

from memory_owner_migration import (
    OWNERSHIP_VERSION,
    OWNERSHIP_VERSION_KEY,
    OwnershipMigrationResult,
    migrate_legacy_ownership,
    require_ownership_ready,
)
from memory_authorization import MemoryPrincipal


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


class FakePGVectorStore:
    """Small in-memory approximation of the two pgvector migration seams."""

    def __init__(self, rows, *, interrupt_at=None, fail_verification=False):
        self.rows = rows
        self.interrupt_at = interrupt_at
        self.fail_verification = fail_verification
        self.patch_count = 0
        self.list_count = 0

    def list(self, *, top_k=None):
        assert top_k is None
        self.list_count += 1
        if self.fail_verification and self.list_count > 1:
            return [[SimpleNamespace(id=self.rows[0].id, payload={"data": "stale"})]]
        return [self.rows]

    def _patch_payload(self, memory_id, fields, *, expected=None):
        self.patch_count += 1
        if self.interrupt_at == self.patch_count:
            raise RuntimeError("connection interrupted")
        memory = next(item for item in self.rows if item.id == memory_id)
        for key, value in (expected or {}).items():
            if memory.payload.get(key) != value:
                return None
        memory.payload.update(fields)
        return memory


class AdvisoryLockCoordinator:
    """Model PostgreSQL transaction advisory-lock ownership for concurrency tests."""

    def __init__(self):
        self.lock = threading.Lock()
        self.acquisitions = 0
        self.active = 0
        self.release_reasons = []


class AdvisoryLockSession(Session):
    def execute(self, statement, params=None, **kwargs):
        if "pg_advisory_xact_lock" in str(statement):
            coordinator = self.info["advisory_lock"]
            coordinator.lock.acquire()
            coordinator.acquisitions += 1
            coordinator.active += 1
            self.info["owns_advisory_lock"] = True
            return MagicMock(name="advisory_lock_result")
        return super().execute(statement, params=params, **kwargs)

    def _release_advisory_lock(self, reason):
        if not self.info.pop("owns_advisory_lock", False):
            return
        coordinator = self.info["advisory_lock"]
        coordinator.active -= 1
        coordinator.release_reasons.append(reason)
        coordinator.lock.release()

    def commit(self):
        try:
            return super().commit()
        finally:
            self._release_advisory_lock("commit")

    def rollback(self):
        try:
            return super().rollback()
        finally:
            self._release_advisory_lock("rollback")

    def close(self):
        self._release_advisory_lock("close")
        return super().close()


def row(memory_id, payload):
    return SimpleNamespace(id=memory_id, payload=dict(payload))


def admin(user_id=None):
    return User(
        id=user_id or uuid.uuid4(),
        name="Administrator",
        email=f"admin-{uuid.uuid4()}@example.com",
        password_hash="unused",
        role="admin",
    )


def member():
    return User(
        id=uuid.uuid4(),
        name="Member",
        email=f"member-{uuid.uuid4()}@example.com",
        password_hash="unused",
        role="member",
    )


def job(owner_id):
    return CategoryJob(memory_id=f"memory-{uuid.uuid4()}", owner_id=owner_id, catalog_snapshot=[])


@pytest.fixture
def admin_id():
    return uuid.uuid4()


@pytest.fixture
def migration_context():
    engines = []

    def build(
        *,
        memories,
        users,
        jobs,
        provider="pgvector",
        interrupt_at=None,
        fail_verification=False,
        autoflush=True,
    ):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        engines.append(engine)
        Base.metadata.create_all(engine, tables=[User.__table__, Settings.__table__, CategoryJob.__table__])
        advisory_lock = AdvisoryLockCoordinator()
        sessions = sessionmaker(
            bind=engine,
            class_=AdvisoryLockSession,
            autoflush=autoflush,
            expire_on_commit=False,
            info={"advisory_lock": advisory_lock},
        )
        with sessions() as session:
            session.add_all([*users, *jobs])
            session.commit()

        vector_store = FakePGVectorStore(
            memories,
            interrupt_at=interrupt_at,
            fail_verification=fail_verification,
        )
        memory = SimpleNamespace(
            config=SimpleNamespace(vector_store=SimpleNamespace(provider=provider)),
            vector_store=vector_store,
        )
        return {
            "session_factory": sessions,
            "memory_factory": lambda: memory,
            "memory": memory,
            "vector_store": vector_store,
            "advisory_lock": advisory_lock,
        }

    yield build

    for engine in engines:
        engine.dispose()


def stored_version(session_factory):
    with session_factory() as session:
        row_value = session.get(Settings, OWNERSHIP_VERSION_KEY)
        return row_value.value if row_value is not None else None


def migrate(context):
    return migrate_legacy_ownership(
        session_factory=context["session_factory"],
        memory_factory=context["memory_factory"],
    )


def test_empty_install_marks_version_ready(migration_context):
    context = migration_context(memories=[], users=[], jobs=[])

    result = migrate(context)

    assert result.state == "ready"
    assert result.migrated_memories == 0
    assert result.migrated_jobs == 0
    assert stored_version(context["session_factory"]) == OWNERSHIP_VERSION
    require_ownership_ready(context["session_factory"])


def test_sole_admin_claims_every_memory_and_job(migration_context, admin_id):
    memories = [
        row("m1", {"data": "one", "user_id": "legacy", "agent_id": "agent-a"}),
        row("m2", {"data": "two", "categories": ["work"]}),
    ]
    context = migration_context(
        memories=memories,
        users=[admin(admin_id)],
        jobs=[job(None), job(admin_id)],
    )

    result = migrate(context)

    assert result.state == "ready"
    assert result.migrated_memories == 2
    assert result.migrated_jobs == 1
    assert all(item.payload["user_id"] == str(admin_id) for item in memories)
    assert memories[0].payload["agent_id"] == "agent-a"
    assert memories[1].payload["categories"] == ["work"]
    with context["session_factory"]() as session:
        assert set(session.scalars(select(CategoryJob.owner_id)).all()) == {admin_id}
    assert migrate(context) == ("ready", 0, 0)
    assert context["vector_store"].patch_count == 2


def test_foreign_category_job_blocks_marker_instead_of_being_accepted(migration_context, admin_id, caplog):
    foreign_owner = uuid.uuid4()
    context = migration_context(
        memories=[row("m1", {"data": "canonical", "user_id": str(admin_id)})],
        users=[admin(admin_id)],
        jobs=[job(admin_id), job(None), job(foreign_owner)],
    )

    with caplog.at_level("ERROR"):
        result = migrate(context)

    assert result == ("blocked", 0, 0)
    assert stored_version(context["session_factory"]) is None
    assert "reason_code=foreign_category_job" in caplog.text
    assert str(foreign_owner) not in caplog.text


def test_claims_distinct_legacy_owners_without_rewriting_canonical_admin_row(migration_context, admin_id):
    canonical_payload = {"data": "canonical", "user_id": str(admin_id), "custom": {"preserve": True}}
    memories = [
        row("legacy-a", {"data": "one", "user_id": "legacy-account-a", "agent_id": "agent-a"}),
        row("legacy-b", {"data": "two", "user_id": "legacy-account-b", "categories": ["work"]}),
        row("canonical", canonical_payload),
    ]
    context = migration_context(memories=memories, users=[admin(admin_id)], jobs=[])

    result = migrate(context)

    assert result == ("ready", 2, 0)
    assert memories[0].payload == {
        "data": "one",
        "user_id": str(admin_id),
        "agent_id": "agent-a",
    }
    assert memories[1].payload == {
        "data": "two",
        "user_id": str(admin_id),
        "categories": ["work"],
    }
    assert memories[2].payload == canonical_payload
    assert context["vector_store"].patch_count == 2


def test_job_verification_flushes_with_production_autoflush_disabled(migration_context, admin_id):
    context = migration_context(
        memories=[],
        users=[admin(admin_id)],
        jobs=[job(None)],
        autoflush=False,
    )

    result = migrate(context)

    assert result == ("ready", 0, 1)
    assert stored_version(context["session_factory"]) == OWNERSHIP_VERSION
    with context["session_factory"]() as session:
        assert session.scalars(select(CategoryJob.owner_id)).all() == [admin_id]


def test_preserves_every_existing_payload_field_except_user_id(migration_context, admin_id):
    payload = {
        "data": "one",
        "user_id": "legacy",
        "agent_id": "agent-a",
        "categories": ["work"],
        "category_status": "completed",
        "custom": {"nested": ["value"]},
    }
    memory = row("m1", payload)
    context = migration_context(memories=[memory], users=[admin(admin_id)], jobs=[])

    assert migrate(context).state == "ready"
    assert memory.payload == {**payload, "user_id": str(admin_id)}


def test_multiple_preexisting_accounts_fail_closed(migration_context):
    context = migration_context(memories=[row("m1", {"user_id": "legacy"})], users=[admin(), member()], jobs=[])

    result = migrate(context)

    assert result.state == "blocked"
    assert stored_version(context["session_factory"]) is None
    with pytest.raises(HTTPException) as error:
        require_ownership_ready(context["session_factory"])
    assert error.value.status_code == 503


def test_multiple_accounts_log_non_sensitive_reason_code(migration_context, caplog):
    context = migration_context(
        memories=[row("m1", {"data": "private-memory", "user_id": "private-legacy-owner"})],
        users=[admin(), member()],
        jobs=[],
    )

    with caplog.at_level("ERROR"):
        assert migrate(context).state == "blocked"

    assert "reason_code=multiple_accounts" in caplog.text
    assert "private-memory" not in caplog.text
    assert "private-legacy-owner" not in caplog.text


def test_legacy_data_waits_for_the_first_admin(migration_context):
    context = migration_context(memories=[row("m1", {"data": "legacy"})], users=[], jobs=[job(None)])

    result = migrate(context)

    assert result.state == "waiting_for_admin"
    assert context["vector_store"].patch_count == 0
    assert stored_version(context["session_factory"]) is None


def test_unsupported_vector_provider_fails_closed(migration_context):
    context = migration_context(
        memories=[row("m1", {"data": "legacy"})],
        users=[admin()],
        jobs=[],
        provider="qdrant",
    )

    result = migrate(context)

    assert result.state == "blocked"
    assert context["vector_store"].patch_count == 0
    assert stored_version(context["session_factory"]) is None


def test_unsupported_provider_logs_reason_without_provider_name(migration_context, caplog):
    context = migration_context(
        memories=[row("m1", {"data": "provider-private-memory"})],
        users=[admin()],
        jobs=[],
        provider="private-provider-canary",
    )

    with caplog.at_level("ERROR"):
        assert migrate(context).state == "blocked"

    assert "reason_code=unsupported_provider" in caplog.text
    assert "private-provider-canary" not in caplog.text
    assert "provider-private-memory" not in caplog.text


def test_invalid_memory_record_logs_reason_without_record_content(migration_context, caplog):
    invalid = SimpleNamespace(id="private-record-id", payload=None)
    context = migration_context(memories=[invalid], users=[admin()], jobs=[])

    with caplog.at_level("ERROR"):
        assert migrate(context).state == "blocked"

    assert "reason_code=invalid_memory_record" in caplog.text
    assert "private-record-id" not in caplog.text


def test_concurrent_patch_logs_reason_and_exception_class_without_exception_message(
    migration_context, admin_id, caplog
):
    context = migration_context(
        memories=[row("m1", {"data": "concurrent-private-memory", "user_id": "legacy"})],
        users=[admin(admin_id)],
        jobs=[],
    )
    context["vector_store"]._patch_payload = lambda *_args, **_kwargs: None

    with caplog.at_level("ERROR"):
        assert migrate(context).state == "blocked"

    assert "reason_code=concurrent_memory_patch" in caplog.text
    assert "exception_class=" in caplog.text
    assert "concurrent-private-memory" not in caplog.text


def test_interrupted_claim_reruns_safely_and_marks_version_only_after_completion(migration_context, admin_id):
    memories = [row("m1", {"data": "one"}), row("m2", {"data": "two"})]
    context = migration_context(memories=memories, users=[admin(admin_id)], jobs=[job(None)], interrupt_at=2)

    interrupted = migrate(context)

    assert interrupted.state == "blocked"
    assert stored_version(context["session_factory"]) is None
    context["vector_store"].interrupt_at = None
    completed = migrate(context)

    # The first row was already durably claimed before the interruption. A retry
    # preserves that canonical row and reports only the remaining legacy write.
    assert completed == ("ready", 1, 1)
    assert stored_version(context["session_factory"]) == OWNERSHIP_VERSION
    assert all(item.payload["user_id"] == str(admin_id) for item in memories)


def test_failed_post_write_verification_keeps_migration_unready(migration_context, admin_id):
    context = migration_context(
        memories=[row("m1", {"data": "one"})],
        users=[admin(admin_id)],
        jobs=[job(None)],
        fail_verification=True,
    )

    result = migrate(context)

    assert result.state == "blocked"
    assert stored_version(context["session_factory"]) is None
    with pytest.raises(HTTPException, match="maintenance"):
        require_ownership_ready(context["session_factory"])


def test_verification_failure_logs_reason_without_memory_content(migration_context, admin_id, caplog):
    context = migration_context(
        memories=[row("m1", {"data": "verification-private-memory"})],
        users=[admin(admin_id)],
        jobs=[],
        fail_verification=True,
    )

    with caplog.at_level("ERROR"):
        assert migrate(context).state == "blocked"

    assert "reason_code=verification_failed" in caplog.text
    assert "exception_class=" in caplog.text
    assert "verification-private-memory" not in caplog.text


def test_advisory_lock_is_released_by_successful_commit(migration_context):
    context = migration_context(memories=[], users=[], jobs=[])

    assert migrate(context).state == "ready"

    assert context["advisory_lock"].acquisitions == 1
    assert context["advisory_lock"].active == 0
    assert context["advisory_lock"].release_reasons == ["commit"]


def test_advisory_lock_is_released_by_failed_migration_rollback(migration_context, admin_id):
    context = migration_context(
        memories=[row("m1", {"data": "one"})],
        users=[admin(admin_id)],
        jobs=[],
        interrupt_at=1,
    )

    assert migrate(context).state == "blocked"

    assert context["advisory_lock"].acquisitions == 1
    assert context["advisory_lock"].active == 0
    assert context["advisory_lock"].release_reasons == ["rollback"]


def test_contending_migration_waits_then_rechecks_ready_marker(migration_context, admin_id):
    context = migration_context(
        memories=[row("m1", {"data": "one"})],
        users=[admin(admin_id)],
        jobs=[],
    )
    first_patch_started = threading.Event()
    release_first_patch = threading.Event()
    second_memory_factory_called = threading.Event()
    original_patch = context["vector_store"]._patch_payload

    def blocking_patch(*args, **kwargs):
        first_patch_started.set()
        assert release_first_patch.wait(timeout=2)
        return original_patch(*args, **kwargs)

    context["vector_store"]._patch_payload = blocking_patch
    results = []
    first = threading.Thread(target=lambda: results.append(migrate(context)))

    def second_memory_factory():
        second_memory_factory_called.set()
        return context["memory"]

    second = threading.Thread(
        target=lambda: results.append(
            migrate_legacy_ownership(
                session_factory=context["session_factory"],
                memory_factory=second_memory_factory,
            )
        )
    )
    first.start()
    assert first_patch_started.wait(timeout=2)
    second.start()
    try:
        assert not second_memory_factory_called.wait(timeout=0.2)
    finally:
        release_first_patch.set()
        first.join(timeout=2)
        second.join(timeout=2)

    assert not first.is_alive()
    assert not second.is_alive()
    assert sorted(results) == [("ready", 0, 0), ("ready", 1, 0)]
    assert context["advisory_lock"].acquisitions == 2
    assert context["advisory_lock"].active == 0


def test_category_lifespan_starts_worker_only_after_ready_migration(monkeypatch):
    """A blocked ownership claim must never let the category worker process legacy data."""
    memory = MagicMock(name="memory")
    worker = MagicMock(name="worker")
    initialized = MagicMock(name="initialize")

    with patch.dict(os.environ, {"AUTH_DISABLED": "true", "OPENAI_API_KEY": "test-key"}, clear=False):
        import auth

        importlib.reload(auth)
        with patch("mem0.Memory.from_config", return_value=memory):
            import server.main as main

            importlib.reload(main)
        monkeypatch.setattr(
            main,
            "migrate_legacy_ownership",
            MagicMock(
                side_effect=[
                    OwnershipMigrationResult("blocked", 0, 0),
                    OwnershipMigrationResult("ready", 0, 0),
                ]
            ),
        )
        monkeypatch.setattr(main, "initialize_category_runtime", initialized)
        monkeypatch.setattr(main, "get_category_worker", MagicMock(return_value=worker))

        async def run_lifespans():
            async with main.category_lifespan(main.app):
                pass
            async with main.category_lifespan(main.app):
                pass

        asyncio.run(run_lifespans())

    initialized.assert_called_once_with()
    worker.stop.assert_called_once_with()


def test_category_route_cannot_lazy_start_runtime_during_blocked_lifespan(monkeypatch):
    """Route dependency access must preserve a still-active lifespan's fail-closed state."""
    memory = MagicMock(name="memory")
    initialize = MagicMock(name="initialize_category_runtime")

    with patch.dict(os.environ, {"AUTH_DISABLED": "true", "OPENAI_API_KEY": "test-key"}, clear=False):
        import auth
        import category_runtime
        from routers import categories as categories_router

        importlib.reload(auth)
        with patch("mem0.Memory.from_config", return_value=memory):
            import server.main as main

            importlib.reload(main)
        monkeypatch.setattr(main, "migrate_legacy_ownership", lambda: OwnershipMigrationResult("blocked", 0, 0))
        monkeypatch.setattr(category_runtime, "_service", None)
        monkeypatch.setattr(category_runtime, "_worker", None)
        monkeypatch.setattr(category_runtime, "initialize_category_runtime", initialize)

        def blocked_readiness():
            raise HTTPException(status_code=503, detail="maintenance")

        monkeypatch.setattr(category_runtime, "require_ownership_ready", blocked_readiness, raising=False)
        main._should_log_request = lambda _request: False
        main.app.dependency_overrides[categories_router.require_memory_principal] = lambda: MemoryPrincipal(
            owner_id="00000000-0000-0000-0000-000000000001"
        )

        try:
            with TestClient(main.app, raise_server_exceptions=False) as client:
                response = client.get("/categories")
        finally:
            main.app.dependency_overrides.clear()

    assert response.status_code == 503
    initialize.assert_not_called()


def test_successful_registration_starts_runtime_after_waiting_state(monkeypatch):
    """First-admin bootstrap must transition a waiting process into worker-ready state."""
    from db import get_db
    from routers import auth as auth_router

    initialize = MagicMock(name="initialize_category_runtime")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[User.__table__, RefreshTokenJti.__table__])
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    app = FastAPI()
    app.include_router(auth_router.router)
    app.dependency_overrides[get_db] = lambda: session
    monkeypatch.setattr(
        auth_router,
        "migrate_legacy_ownership",
        lambda: OwnershipMigrationResult("ready", 1, 0),
    )
    monkeypatch.setattr(auth_router, "initialize_category_runtime", initialize)
    monkeypatch.setattr(auth_router, "create_access_token", lambda *_args: "access-token")
    monkeypatch.setattr(auth_router, "create_refresh_token", lambda *_args: "refresh-token")
    try:
        response = TestClient(app).post(
            "/auth/register",
            json={"name": "Admin", "email": "admin@example.com", "password": "long-enough-password"},
        )
    finally:
        session.close()
        engine.dispose()

    assert response.status_code == 200, response.text
    initialize.assert_called_once_with()


def test_waiting_lifespan_stops_worker_started_by_successful_registration(monkeypatch):
    """A runtime started after bootstrap remains owned by the already-active app lifespan."""
    from db import get_db
    from routers import auth as auth_router

    worker = MagicMock(name="worker")
    service = MagicMock(name="service")
    memory = MagicMock(name="memory")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[User.__table__])
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    auth_app = FastAPI()
    auth_app.include_router(auth_router.router)
    auth_app.dependency_overrides[get_db] = lambda: session

    with patch.dict(os.environ, {"AUTH_DISABLED": "true", "OPENAI_API_KEY": "test-key"}, clear=False):
        import auth
        import category_runtime

        importlib.reload(auth)
        with patch("mem0.Memory.from_config", return_value=memory):
            import server.main as main

            importlib.reload(main)
        monkeypatch.setattr(
            main, "migrate_legacy_ownership", lambda: OwnershipMigrationResult("waiting_for_admin", 0, 0)
        )
        monkeypatch.setattr(category_runtime, "_service", None)
        monkeypatch.setattr(category_runtime, "_worker", None)
        monkeypatch.setattr(
            auth_router,
            "migrate_legacy_ownership",
            lambda: OwnershipMigrationResult("ready", 0, 1),
        )

        def start_runtime():
            category_runtime._service = service
            category_runtime._worker = worker
            return service

        monkeypatch.setattr(auth_router, "initialize_category_runtime", start_runtime)
        monkeypatch.setattr(auth_router, "create_access_token", lambda *_args: "access-token")
        monkeypatch.setattr(auth_router, "create_refresh_token", lambda *_args: "refresh-token")

        async def registration_during_lifespan():
            async with main.category_lifespan(main.app):
                response = TestClient(auth_app).post(
                    "/auth/register",
                    json={"name": "Admin", "email": "transition@example.com", "password": "long-enough-password"},
                )
                assert response.status_code == 200, response.text
                worker.stop.assert_not_called()

        try:
            asyncio.run(registration_during_lifespan())
        finally:
            session.close()
            engine.dispose()

    worker.stop.assert_called_once_with()


def test_register_returns_maintenance_when_ownership_migration_cannot_complete(monkeypatch):
    """The newly committed administrator remains usable even when claiming legacy data fails."""
    from db import get_db
    import auth
    from routers import auth as auth_router

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[User.__table__])
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    app = FastAPI()
    app.include_router(auth_router.router)
    app.dependency_overrides[get_db] = lambda: session
    monkeypatch.setattr(
        auth_router,
        "migrate_legacy_ownership",
        lambda: OwnershipMigrationResult("blocked", 0, 0),
    )
    monkeypatch.setattr(auth, "JWT_SECRET", "test-jwt-secret")
    try:
        response = TestClient(app).post(
            "/auth/register",
            json={"name": "Admin", "email": "admin@example.com", "password": "long-enough-password"},
        )

        assert response.status_code == 503
        assert response.json() == {"detail": "Memory ownership migration is in maintenance. Please try again later."}
        assert session.scalar(select(User.email)) == "admin@example.com"
    finally:
        session.close()
        engine.dispose()
