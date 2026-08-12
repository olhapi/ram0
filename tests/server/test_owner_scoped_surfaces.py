"""Ownership boundaries for entity and category-adjacent server surfaces."""

import uuid
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from auth import verify_auth
from category_models import CategoryDefinition
from category_runtime import get_category_service
from category_service import CategoryService
from category_store import CategoryJobStore, EnqueueResult, MemoryCategoryStore, MemorySnapshot
from memory_authorization import MemoryPrincipal, require_memory_principal
from models import Base, CategoryJob
from routers import categories as categories_router
from routers import entities as entities_router


OWNER_A = "00000000-0000-0000-0000-000000000001"
OWNER_B = "00000000-0000-0000-0000-000000000002"


@pytest.fixture
def job_store():
    store = MagicMock(name="job_store")
    store.memory_fence.return_value = nullcontext()
    store.prepare.return_value = EnqueueResult(job_id=uuid.uuid4(), created=True)
    store.install_prepared.return_value = True
    store.active_matches.return_value = False
    return store


@pytest.fixture
def memory_store():
    store = MagicMock(name="memory_store")
    store.mark_pending.side_effect = lambda memory_id, generation, **_kwargs: store.get(memory_id)
    return store


@pytest.fixture
def service(job_store, memory_store):
    catalog_store = MagicMock(name="catalog_store")
    catalog_store.get_saved.return_value = (
        CategoryDefinition(name="billing", description="Invoices and payments."),
    )
    return CategoryService(catalog_store, job_store, memory_store, MagicMock(name="classifier"))


@pytest.fixture
def entity_client(monkeypatch):
    vector_store = MagicMock(name="vector_store")
    vector_store.list.return_value = [
        [
            SimpleNamespace(
                id="memory-a",
                payload={"user_id": OWNER_A, "agent_id": "agent-a", "run_id": "run-a"},
            )
        ]
    ]
    memory = SimpleNamespace(vector_store=vector_store, delete_all=MagicMock(name="delete_all"))
    monkeypatch.setattr(
        entities_router,
        "get_memory_instance",
        lambda: memory,
    )
    app = FastAPI()
    app.include_router(entities_router.router)
    app.dependency_overrides[verify_auth] = lambda: object()
    app.dependency_overrides[require_memory_principal] = lambda: MemoryPrincipal(owner_id=OWNER_A)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, memory


def test_entities_scan_only_the_principal_owner(entity_client):
    """Dropping the canonical owner filter exposes every account's derived entities."""
    client, memory = entity_client

    response = client.get("/entities")

    assert response.status_code == 200
    assert memory.vector_store.list.call_args.kwargs["filters"] == {"user_id": OWNER_A}
    assert {item["type"] for item in response.json()} == {"agent", "run"}


@pytest.mark.parametrize(("entity_type", "field"), [("agent", "agent_id"), ("run", "run_id")])
def test_entity_delete_combines_shared_identifier_with_the_principal_owner(entity_client, entity_type, field):
    """A shared agent/run ID must delete only the authenticated owner's memories."""
    client, memory = entity_client

    response = client.delete(f"/entities/{entity_type}/shared-id")

    assert response.status_code == 200
    memory.delete_all.assert_called_once_with(user_id=OWNER_A, **{field: "shared-id"})


def test_category_counts_are_owner_scoped():
    """A global count query leaks another account's category usage into the catalog view."""
    vector_store = MagicMock(name="vector_store")
    vector_store.list.return_value = [[]]
    memory_store = MemoryCategoryStore(lambda: SimpleNamespace(vector_store=vector_store))

    assert memory_store.category_counts(OWNER_A) == {}
    assert vector_store.list.call_args.kwargs["filters"] == {"user_id": OWNER_A}


def test_reclassification_only_enqueues_the_admin_owner_memory(service, memory_store, job_store):
    """A global reclassification scan lets one administrator queue jobs for another account."""
    owner_a_snapshot = _snapshot("memory-a", OWNER_A)
    owner_b_snapshot = _snapshot("memory-b", OWNER_B)
    memory_store.iter_snapshots.side_effect = lambda owner_id: [owner_a_snapshot] if owner_id == OWNER_A else [owner_b_snapshot]
    snapshots = {snapshot.memory_id: snapshot for snapshot in (owner_a_snapshot, owner_b_snapshot)}
    memory_store.get.side_effect = snapshots.__getitem__

    result = service.start_reclassification(scope="all", confirm="RECLASSIFY", owner_id=OWNER_A)

    assert result.eligible_memories == 1
    assert memory_store.iter_snapshots.call_args.args == (OWNER_A,)
    assert job_store.prepare.call_args.kwargs["owner_id"] == uuid.UUID(OWNER_A)


def test_job_polling_scopes_route_through_service_and_store_to_the_member_owner():
    """Dropping owner propagation at any layer exposes another account's memory ID."""

    @compiles(JSONB, "sqlite")
    def compile_jsonb_as_json(_type, _compiler, **_kwargs):
        return "JSON"

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[CategoryJob.__table__])
    session_factory = sessionmaker(bind=engine)
    with session_factory() as session:
        session.add_all(
            [
                CategoryJob(memory_id="memory-a", owner_id=uuid.UUID(OWNER_A), catalog_snapshot=[]),
                CategoryJob(memory_id="memory-b", owner_id=uuid.UUID(OWNER_B), catalog_snapshot=[]),
            ]
        )
        session.commit()

    scoped_service = CategoryService(
        MagicMock(name="catalog_store"),
        CategoryJobStore(session_factory),
        MagicMock(name="memory_store"),
        MagicMock(name="classifier"),
    )
    app = FastAPI()
    app.include_router(categories_router.router)
    app.dependency_overrides[require_memory_principal] = lambda: MemoryPrincipal(owner_id=OWNER_A)
    app.dependency_overrides[get_category_service] = lambda: scoped_service

    with TestClient(app) as client:
        response = client.get("/categories/jobs")

    assert response.status_code == 200
    assert [job["memory_id"] for job in response.json()] == ["memory-a"]
    engine.dispose()


def _snapshot(memory_id: str, owner_id: str):
    return MemorySnapshot(
        memory_id=memory_id,
        user_id=owner_id,
        text="Memory text",
        memory_hash="hash",
        categories=None,
        category_status="unclassified",
        category_generation=None,
        category_origin=None,
        payload={"user_id": owner_id},
    )
