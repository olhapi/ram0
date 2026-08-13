"""End-to-end category lifecycle coverage for the main memory routes."""

import importlib
import os
import threading
import uuid
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")

from fastapi.testclient import TestClient

from category_models import CategoryDefinition, EffectiveCatalog
from category_store import MemoryCategoryStore
from memory_owner_migration import OwnershipMigrationResult


@pytest.fixture
def mock_memory():
    memory = MagicMock()
    memory.add.return_value = {"results": [{"id": "m1", "event": "ADD", "memory": "Invoice"}]}
    memory.get.return_value = {"id": "m1", "memory": "old", "metadata": {"source": "x"}}
    memory.get_all.return_value = {"results": [{"id": "m1", "memory": "old", "metadata": {"source": "x"}}]}
    memory.search.return_value = {"results": [{"id": "m1", "memory": "old", "metadata": {"source": "x"}}]}
    memory.update.return_value = {"id": "m1", "memory": "updated", "metadata": {"source": "x"}}
    memory.delete.return_value = None
    memory.reset.return_value = None
    memory.vector_store.get.return_value = SimpleNamespace(id="m1", payload={"user_id": "u1", "data": "old"})
    memory.vector_store.list.return_value = [
        [
            SimpleNamespace(
                id="m1",
                payload={"data": "old", "categories": ["billing"], "category_status": "completed"},
            )
        ]
    ]
    return memory


@pytest.fixture
def category_service():
    service = MagicMock()
    request_catalog = EffectiveCatalog(
        definitions=(CategoryDefinition(name="billing", description="Invoices"),), source="request"
    )
    service.resolve_catalog.return_value = request_catalog
    service.after_add.side_effect = lambda response, _catalog, **_kwargs: response
    service.run_memory_update.side_effect = lambda _memory_id, operation, **_kwargs: operation()
    service.owner_fence.side_effect = lambda _owner_id: nullcontext(object())
    service.after_owner_reset.return_value = True
    return service


@pytest.fixture
def app_module(mock_memory, category_service):
    worker = MagicMock()
    runtime_service = MagicMock()

    with patch.dict(os.environ, {"AUTH_DISABLED": "true", "OPENAI_API_KEY": "test-key"}, clear=False):
        import auth
        import category_runtime

        importlib.reload(auth)
        with patch("mem0.Memory.from_config", return_value=mock_memory):
            import server.main as server_main

            with patch.object(category_runtime, "initialize_category_runtime", return_value=runtime_service) as initialize:
                with patch.object(category_runtime, "get_category_worker", return_value=worker):
                    importlib.reload(server_main)
                    server_main.get_category_service = MagicMock(return_value=category_service)
                    server_main.migrate_legacy_ownership = MagicMock(
                        return_value=OwnershipMigrationResult("ready", 0, 0)
                    )
                    server_main.app.dependency_overrides[server_main.require_admin] = lambda: None
                    server_main.app.dependency_overrides[server_main.require_memory_principal] = lambda: (
                        server_main.MemoryPrincipal(owner_id="u1")
                    )
                    server_main._should_log_request = lambda _request: False
                    yield server_main, worker, initialize


@pytest.fixture
def client(app_module):
    with TestClient(app_module[0].app, raise_server_exceptions=False) as test_client:
        yield test_client


def test_add_uses_request_catalog_without_forwarding_it_to_core(client, mock_memory, category_service):
    response = client.post(
        "/memories",
        json={
            "messages": [{"role": "user", "content": "Invoice"}],
            "custom_categories": [{"billing": "Invoices"}],
        },
    )

    assert response.status_code == 200, response.text
    assert "custom_categories" not in mock_memory.add.call_args.kwargs
    origin = mock_memory.add.call_args.kwargs["metadata"]["_category_origin"]
    assert isinstance(origin, str)
    assert category_service.resolve_catalog.call_args.args[0] == "u1"
    assert category_service.resolve_catalog.call_args.args[1][0].name == "billing"
    assert category_service.after_add.call_args.kwargs == {"origin_token": origin}
    assert "_category_origin" not in response.text


def test_add_snapshots_effective_catalog_before_slow_core_write(
    client, mock_memory, category_service
):
    original = EffectiveCatalog(
        definitions=(CategoryDefinition(name="billing", description="Invoices"),),
        source="project",
    )
    edited = EffectiveCatalog(
        definitions=(CategoryDefinition(name="support", description="Cases"),),
        source="project",
    )
    events = []

    def resolve(owner_id, _request_catalog):
        assert owner_id == "u1"
        events.append("resolve")
        return original

    def slow_add(*_args, **_kwargs):
        events.append("add")
        category_service.resolve_catalog.return_value = edited
        return {"results": [{"id": "m1", "event": "ADD", "memory": "Invoice"}]}

    def after_add(response, catalog, **_kwargs):
        events.append("after_add")
        assert catalog == original
        return response

    category_service.resolve_catalog.side_effect = resolve
    category_service.after_add.side_effect = after_add
    mock_memory.add.side_effect = slow_add

    response = client.post(
        "/memories",
        json={"messages": [{"role": "user", "content": "Invoice"}]},
    )

    assert response.status_code == 200
    assert events == ["resolve", "add", "after_add"]
    category_service.resolve_catalog.assert_called_once_with("u1", None)
    assert category_service.after_add.call_args.args[1] == original


@pytest.mark.parametrize("categories", [[], [{"billing": "Invoices", "health": "Care"}]])
def test_add_rejects_empty_or_malformed_request_catalog(client, mock_memory, category_service, categories):
    response = client.post(
        "/memories",
        json={
            "messages": [{"role": "user", "content": "Invoice"}],
            "custom_categories": categories,
        },
    )

    assert response.status_code == 422
    mock_memory.add.assert_not_called()
    category_service.after_add.assert_not_called()


def test_add_category_failure_preserves_successful_core_response(client, category_service):
    category_service.after_add.side_effect = RuntimeError("database unavailable")

    response = client.post(
        "/memories",
        json={"messages": [{"role": "user", "content": "Invoice"}]},
    )

    assert response.status_code == 200
    assert response.json()["results"][0]["category_status"] == "unclassified"


def test_app_id_survives_category_failure_and_update_route_projection(
    client, mock_memory, category_service
):
    """Category reconciliation must not erase the project scope returned by the core memory layer."""
    app_id = "github.com-olhapi-ram0"
    mock_memory.add.return_value = {
        "results": [{"id": "m1", "event": "ADD", "memory": "Invoice", "app_id": app_id}]
    }
    category_service.after_add.side_effect = RuntimeError("database unavailable")

    added = client.post(
        "/memories",
        json={"messages": [{"role": "user", "content": "Invoice"}], "app_id": app_id},
    )

    assert added.status_code == 200, added.text
    assert added.json()["results"][0]["app_id"] == app_id

    category_service.run_memory_update.side_effect = lambda _memory_id, operation, **_kwargs: operation()
    mock_memory.update.return_value = {"id": "m1", "memory": "updated", "app_id": app_id}
    updated = client.put("/memories/m1", json={"text": "Updated invoice"})

    assert updated.status_code == 200, updated.text
    assert updated.json()["app_id"] == app_id


@pytest.mark.parametrize("transition", ["prepare", "classify", "fail", "restore"])
def test_category_payload_transitions_preserve_app_id(transition):
    """Category-only payload patches must retain the native app scope field."""
    app_id = "github.com-olhapi-ram0"
    owner_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    payload = {
        "user_id": str(owner_id),
        "app_id": app_id,
        "data": "Invoice",
        "hash": "hash-1",
        "categories": ["billing"],
        "category_status": "completed",
        "_category_generation": "old-generation",
        "_category_origin": "origin-1",
    }
    row = SimpleNamespace(id="m1", payload=payload)
    vector_store = MagicMock()
    vector_store.get.return_value = row

    def patch_payload(_memory_id, fields, **_kwargs):
        payload.update(fields)
        return row

    vector_store._patch_payload.side_effect = patch_payload
    store = MemoryCategoryStore(lambda: SimpleNamespace(vector_store=vector_store))
    snapshot = store.get("m1")

    if transition == "prepare":
        result = store.mark_pending("m1", "next-generation", owner_id=owner_id)
        assert result.payload["app_id"] == app_id
    elif transition == "classify":
        assert store.write_result("m1", "hash-1", "old-generation", ["billing"], "completed", owner_id=owner_id)
    elif transition == "fail":
        assert store.fail_origin(snapshot)
    else:
        assert store.restore(snapshot, expected_generation="old-generation")

    assert payload["app_id"] == app_id


def test_every_put_runs_core_update_and_category_reconciliation_under_one_fence(
    client, category_service, mock_memory
):
    assert client.put("/memories/m1", json={"text": "Doctor visit"}).status_code == 200
    assert category_service.run_memory_update.call_args.kwargs["owner_id"] == "u1"
    assert category_service.run_memory_update.call_args.kwargs["supplied_text"] == "Doctor visit"

    category_service.run_memory_update.reset_mock()
    assert client.put("/memories/m1", json={"text": "old", "metadata": {"source": "new"}}).status_code == 200
    assert category_service.run_memory_update.call_args.kwargs["supplied_text"] == "old"

    category_service.run_memory_update.reset_mock()
    assert client.put("/memories/m1", json={"metadata": {"source": "x"}}).status_code == 200
    assert "supplied_text" not in category_service.run_memory_update.call_args.kwargs
    assert mock_memory.update.call_count == 3


def test_put_always_reconciles_after_core_update_using_the_preupdate_text_comparison(
    client, category_service, mock_memory
):
    mock_memory.get.return_value = {"id": "m1", "memory": "A"}

    response = client.put("/memories/m1", json={"text": "A", "metadata": {"race": "restored"}})

    assert response.status_code == 200
    category_service.run_memory_update.assert_called_once()


def test_internal_category_tokens_are_never_exposed_by_account_scoped_list(client, mock_memory):
    mock_memory.get_all.return_value = {
        "results": [
            {
                "id": "m1",
                "memory": "old",
                "metadata": {
                    "_category_generation": "internal-job-token",
                    "_category_origin": "internal-request-token",
                    "source": "x",
                },
            }
        ]
    }

    response = client.get("/memories", params={"top_k": 5})

    assert response.status_code == 200
    memory = response.json()["results"][0]
    assert "_category_generation" not in memory
    assert "_category_origin" not in memory
    assert memory["metadata"] == {"source": "x"}


def test_delete_cancellation_failure_preserves_successful_core_response(client, category_service):
    category_service.after_delete.side_effect = RuntimeError("database unavailable")

    response = client.delete("/memories/m1")

    assert response.status_code == 200
    category_service.after_delete.assert_called_once_with("m1", "u1")


@pytest.mark.parametrize("cleanup_result", [False, RuntimeError("database unavailable: secret")])
def test_reset_reports_generic_partial_failure_when_job_purge_fails(
    client, category_service, mock_memory, cleanup_result
):
    if isinstance(cleanup_result, Exception):
        category_service.after_owner_reset.side_effect = cleanup_result
    else:
        category_service.after_owner_reset.return_value = cleanup_result

    response = client.post("/reset")

    assert response.status_code == 503, response.text
    assert response.json() == {"detail": "Reset cleanup incomplete."}
    assert "secret" not in response.text
    mock_memory.delete_all.assert_called_once_with(user_id="u1")
    category_service.after_owner_reset.assert_called_once_with("u1")


def test_reset_waits_until_memory_add_and_category_enqueue_release_the_owner_fence(
    client, category_service, mock_memory
):
    """A reset cannot delete memories before an in-flight add installs its category job."""
    owner_lock = threading.Lock()
    enqueue_entered = threading.Event()
    release_enqueue = threading.Event()
    reset_delete_called = threading.Event()
    responses = []

    @contextmanager
    def owner_fence(_owner_id):
        with owner_lock:
            yield object()

    def blocked_after_add(response, _catalog, **_kwargs):
        enqueue_entered.set()
        assert release_enqueue.wait(1.0)
        return response

    def reset_delete(**_kwargs):
        reset_delete_called.set()

    category_service.owner_fence.side_effect = owner_fence
    category_service.after_add.side_effect = blocked_after_add
    mock_memory.delete_all.side_effect = reset_delete

    add_thread = threading.Thread(
        target=lambda: responses.append(
            client.post("/memories", json={"messages": [{"role": "user", "content": "Invoice"}]})
        )
    )
    reset_thread = threading.Thread(target=lambda: responses.append(client.post("/reset")))
    add_thread.start()
    assert enqueue_entered.wait(1.0)
    reset_thread.start()

    assert not reset_delete_called.wait(0.1)
    release_enqueue.set()
    add_thread.join(1.0)
    reset_thread.join(1.0)

    assert not add_thread.is_alive()
    assert not reset_thread.is_alive()
    assert sorted(response.status_code for response in responses) == [200, 200]


def test_legacy_memory_get_has_top_level_unclassified_state(client):
    response = client.get("/memories/m1")

    assert response.status_code == 200
    assert response.json()["categories"] is None
    assert response.json()["category_status"] == "unclassified"


def test_get_all_and_search_promote_nested_legacy_category_fields(client):
    get_all = client.get("/memories")
    search = client.post("/search", json={"query": "invoice"})

    assert get_all.status_code == 200
    assert get_all.json()["results"][0]["category_status"] == "unclassified"
    assert search.status_code == 200
    assert search.json()["results"][0]["category_status"] == "unclassified"


def test_get_memories_repeated_categories_are_any_filter(client, mock_memory):
    response = client.get(
        "/memories",
        params=[("categories", "billing"), ("categories", "health")],
    )

    assert response.status_code == 200, response.text
    assert mock_memory.get_all.call_args.kwargs["filters"] == {
        "user_id": "u1",
        "categories": {"in": ["billing", "health"]},
    }


def test_account_list_repeated_categories_are_any_filter(client, mock_memory):
    response = client.get(
        "/memories",
        params=[("categories", "billing"), ("categories", "health"), ("top_k", "7")],
    )

    assert response.status_code == 200, response.text
    assert mock_memory.get_all.call_args.kwargs == {
        "filters": {"user_id": "u1", "categories": {"in": ["billing", "health"]}},
        "top_k": 7,
        "show_expired": False,
    }


def test_search_passes_nested_category_filter_to_core_unchanged(client, mock_memory):
    filters = {"categories": {"in": ["billing", "health"]}}

    response = client.post("/search", json={"query": "invoice", "filters": filters})

    assert response.status_code == 200, response.text
    assert mock_memory.search.call_args.kwargs["filters"] == {"user_id": "u1", **filters}


def test_account_list_emits_category_fields_once(client, mock_memory):
    mock_memory.get_all.return_value = {
        "results": [
            {
                "id": "m1",
                "memory": "old",
                "metadata": {"categories": ["billing"], "category_status": "completed"},
            }
        ]
    }
    response = client.get("/memories")

    assert response.status_code == 200
    memory = response.json()["results"][0]
    assert memory["categories"] == ["billing"]
    assert memory["category_status"] == "completed"
    assert "categories" not in memory["metadata"]
    assert "category_status" not in memory["metadata"]


def test_lifespan_initializes_and_stops_worker_once(app_module):
    main, worker, initialize = app_module

    with TestClient(main.app, raise_server_exceptions=False) as client:
        assert client.get("/", follow_redirects=False).status_code == 307

    initialize.assert_called_once_with()
    worker.stop.assert_called_once_with()
