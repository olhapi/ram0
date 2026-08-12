"""Contract tests for the custom-category administration router."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from category_models import CategoryDefinition, CategoryJobState
from category_runtime import get_category_service
from category_service import CatalogView, ReclassificationPreview, ReclassificationStart
from memory_authorization import MemoryPrincipal, require_memory_principal
from routers import categories as categories_router


DEFINITIONS = (
    CategoryDefinition(name="billing", description="Invoices and payments."),
    CategoryDefinition(name="support", description="Customer support requests."),
)
NOW = datetime(2026, 8, 8, tzinfo=timezone.utc)
OWNER_ID = "00000000-0000-0000-0000-000000000001"


def catalog_view(
    *,
    saved: tuple[CategoryDefinition, ...] = DEFINITIONS,
    active: tuple[CategoryDefinition, ...] = DEFINITIONS,
    source: str = "project",
    counts: dict[str, int] | None = None,
) -> CatalogView:
    return CatalogView(saved=saved, active=active, source=source, counts=counts or {"billing": 3})


@pytest.fixture
def service():
    category_service = MagicMock()
    category_service.get_catalog_view.return_value = catalog_view()
    category_service.create_category.return_value = catalog_view()
    category_service.replace_catalog.return_value = catalog_view()
    category_service.update_category.return_value = catalog_view()
    category_service.delete_category.return_value = catalog_view()
    category_service.preview_reclassification.return_value = ReclassificationPreview(
        scope="unclassified_failed",
        eligible_memories=2,
        estimated_calls=2,
        estimated_input_tokens=120,
        estimated_output_tokens=40,
        estimated_cost=0.00056,
    )
    category_service.start_reclassification.return_value = ReclassificationStart(
        created_jobs=1,
        skipped_active_jobs=1,
        eligible_memories=2,
    )
    category_service.list_jobs.return_value = []
    return category_service


@pytest.fixture
def app(service):
    app = FastAPI()
    app.include_router(categories_router.router)

    app.dependency_overrides[require_memory_principal] = lambda: MemoryPrincipal(owner_id=OWNER_ID)
    app.dependency_overrides[get_category_service] = lambda: service
    return app


@pytest.fixture
def client(app):
    return TestClient(app, raise_server_exceptions=False)


def test_get_categories_distinguishes_saved_active_source_and_counts(client, service):
    defaults = (CategoryDefinition(name="personal_details", description="Identity."),)
    service.get_catalog_view.return_value = catalog_view(saved=(), active=defaults, source="defaults", counts={"retired": 2})

    response = client.get("/categories")

    assert response.status_code == 200
    assert response.json() == {
        "saved": [],
        "active": [{"name": "personal_details", "description": "Identity."}],
        "source": "defaults",
        "counts": {"retired": 2},
        "retired": [{"name": "retired", "count": 2}],
    }
    service.get_catalog_view.assert_called_once_with(OWNER_ID)


@pytest.mark.parametrize(
    ("method", "path", "body", "status_code"),
    [
        ("get", "/categories", None, 200),
        ("post", "/categories", {"name": "legal", "description": "Legal matters."}, 201),
        ("put", "/categories", [{"name": "legal", "description": "Legal matters."}], 200),
        ("patch", "/categories/billing", {"description": "Billing matters."}, 200),
        ("delete", "/categories/billing", None, 200),
    ],
)
def test_catalog_response_routes_serialize_sorted_retired_counts(client, service, method, path, body, status_code):
    view = catalog_view(counts={"support": 7, "archived_z": 1, "billing": 3, "archived_a": 2})
    service.get_catalog_view.return_value = view
    service.create_category.return_value = view
    service.replace_catalog.return_value = view
    service.update_category.return_value = view
    service.delete_category.return_value = view

    response = getattr(client, method)(path, json=body) if body is not None else getattr(client, method)(path)

    assert response.status_code == status_code
    assert response.json()["source"] == "user"
    assert response.json()["retired"] == [
        {"name": "archived_a", "count": 2},
        {"name": "archived_z", "count": 1},
    ]


def test_get_categories_requires_a_memory_principal(app, client):
    def unauthorized():
        raise HTTPException(status_code=401, detail="Authentication required.")

    app.dependency_overrides[require_memory_principal] = unauthorized

    response = client.get("/categories")

    assert response.status_code == 401


@pytest.mark.parametrize(
    ("method", "path", "body", "service_method"),
    [
        ("post", "/categories", {"name": "legal", "description": "Legal matters."}, "create_category"),
        ("put", "/categories", [], "replace_catalog"),
        ("patch", "/categories/billing", {"description": "Billing matters."}, "update_category"),
        ("delete", "/categories/billing", None, "delete_category"),
        ("get", "/categories/jobs", None, "list_jobs"),
        (
            "post",
            "/categories/reclassify/preview",
            {"scope": "all"},
            "preview_reclassification",
        ),
        (
            "post",
            "/categories/reclassify",
            {"scope": "all", "confirm": "RECLASSIFY"},
            "start_reclassification",
        ),
    ],
)
def test_member_principal_can_manage_only_its_category_controls(
    app, client, service, method, path, body, service_method
):
    response = client.request(method, path, json=body)

    assert response.status_code in {200, 201, 202}
    getattr(service, service_method).assert_called_once()


def test_member_can_replace_only_its_own_catalog(client, service):
    response = client.put("/categories", json=[{"name": "coding", "description": "Code decisions."}])

    assert response.status_code == 200
    service.replace_catalog.assert_called_once_with(
        (CategoryDefinition(name="coding", description="Code decisions."),), OWNER_ID
    )
    assert response.json()["source"] == "user"


def test_post_category_delegates_valid_definition_without_enqueueing(client, service):
    response = client.post("/categories", json={"name": "legal", "description": "Legal matters."})

    assert response.status_code == 201
    service.create_category.assert_called_once_with(CategoryDefinition(name="legal", description="Legal matters."), OWNER_ID)
    service.enqueue_memory.assert_not_called()


@pytest.mark.parametrize(
    "body",
    [
        {"name": "Not snake case", "description": "Valid description."},
        {"name": "legal", "description": ""},
        {"name": "legal", "description": "Valid description.", "unknown": True},
    ],
)
def test_post_category_rejects_invalid_definition(client, service, body):
    response = client.post("/categories", json=body)

    assert response.status_code == 422
    service.create_category.assert_not_called()


def test_put_empty_catalog_resets_defaults(client, service):
    response = client.put("/categories", json=[])

    assert response.status_code == 200
    service.replace_catalog.assert_called_once_with((), OWNER_ID)
    service.enqueue_memory.assert_not_called()


def test_put_catalog_preserves_request_order(client, service):
    response = client.put(
        "/categories",
        json=[
            {"name": "support", "description": "Customer support requests."},
            {"name": "billing", "description": "Invoices and payments."},
        ],
    )

    assert response.status_code == 200
    service.replace_catalog.assert_called_once_with((DEFINITIONS[1], DEFINITIONS[0]), OWNER_ID)


def test_patch_rejects_empty_body(client, service):
    response = client.patch("/categories/billing", json={})

    assert response.status_code == 422
    service.update_category.assert_not_called()


@pytest.mark.parametrize("body", [{"name": None}, {"description": None}, {"name": None, "description": None}])
def test_patch_rejects_null_only_body(client, service, body):
    response = client.patch("/categories/billing", json=body)

    assert response.status_code == 422
    service.update_category.assert_not_called()


def test_patch_merges_the_saved_definition_and_allows_explicit_rename(client, service):
    response = client.patch("/categories/billing", json={"name": "invoices", "description": "Billing invoices."})

    assert response.status_code == 200
    service.update_category.assert_called_once_with(
        "billing", OWNER_ID, new_name="invoices", description="Billing invoices."
    )


def test_patch_unknown_saved_definition_returns_not_found_without_raw_error(client, service):
    service.update_category.side_effect = KeyError("database secret")

    response = client.patch("/categories/missing", json={"description": "Nope."})

    assert response.status_code == 404
    assert response.json()["detail"] == "Category not found."
    service.update_category.assert_called_once_with("missing", OWNER_ID, new_name=None, description="Nope.")


def test_delete_translates_service_not_found_without_raw_error(client, service):
    service.delete_category.side_effect = KeyError("database secret")

    response = client.delete("/categories/missing")

    assert response.status_code == 404
    assert response.json()["detail"] == "Category not found."


def test_static_jobs_route_is_not_captured_as_category_name(client, service):
    job = SimpleNamespace(
        id="job-1",
        memory_id="memory-1",
        state=CategoryJobState.FAILED,
        attempts=3,
        created_at=NOW,
        updated_at=NOW,
        started_at=NOW,
        completed_at=NOW,
        next_attempt_at=None,
        error_code="provider_error",
        error_message="Category provider request failed",
        catalog_snapshot=[{"name": "private", "description": "secret"}],
        worker_id="worker-secret",
        memory_hash="hash-secret",
    )
    service.list_jobs.return_value = [job]

    response = client.get("/categories/jobs?state=failed&limit=5")

    assert response.status_code == 200
    service.list_jobs.assert_called_once_with(owner_id=OWNER_ID, states=(CategoryJobState.FAILED,), limit=5)
    assert response.json() == [
        {
            "id": "job-1",
            "memory_id": "memory-1",
            "state": "failed",
            "attempts": 3,
            "created_at": "2026-08-08T00:00:00Z",
            "updated_at": "2026-08-08T00:00:00Z",
            "started_at": "2026-08-08T00:00:00Z",
            "completed_at": "2026-08-08T00:00:00Z",
            "next_attempt_at": None,
            "error_code": "provider_error",
            "error_message": "Category provider request failed",
        }
    ]
    payload = response.text
    assert "catalog_snapshot" not in payload
    assert "worker-secret" not in payload
    assert "hash-secret" not in payload
    service.delete_category.assert_not_called()


def test_terminalizing_marker_is_never_exposed_by_jobs_api(client, service):
    service.list_jobs.return_value = [
        SimpleNamespace(
            id="job-recovery",
            memory_id="memory-1",
            state=CategoryJobState.RETRYING,
            attempts=3,
            created_at=NOW,
            updated_at=NOW,
            started_at=NOW,
            completed_at=None,
            next_attempt_at=NOW,
            error_code="_terminalizing_2_invalid_json",
            error_message="Invalid category response",
        )
    ]

    response = client.get("/categories/jobs")

    assert response.status_code == 200
    payload = response.json()[0]
    assert payload["state"] == "retrying"
    assert payload["error_code"] == "invalid_json"
    assert "terminalizing" not in response.text


@pytest.mark.parametrize("path", ["/categories/jobs?state=unknown", "/categories/jobs?limit=0", "/categories/jobs?limit=101"])
def test_jobs_reject_invalid_state_or_limit(client, service, path):
    response = client.get(path)

    assert response.status_code == 422
    service.list_jobs.assert_not_called()


def test_preparing_state_is_absent_from_public_query_and_openapi(client, service):
    response = client.get("/categories/jobs?state=preparing")

    assert response.status_code == 422
    assert "preparing" not in str(client.get("/openapi.json").json())
    service.list_jobs.assert_not_called()


def test_preview_accepts_paired_nonnegative_rates(client, service):
    response = client.post(
        "/categories/reclassify/preview",
        json={"scope": "all", "input_rate_per_million": 2.0, "output_rate_per_million": 8.0},
    )

    assert response.status_code == 200
    service.preview_reclassification.assert_called_once_with(
        scope="all", owner_id=OWNER_ID, input_rate_per_million=2.0, output_rate_per_million=8.0
    )
    assert response.json()["estimated_cost"] == pytest.approx(0.00056)


@pytest.mark.parametrize(
    "body",
    [
        {"input_rate_per_million": -1.0, "output_rate_per_million": 0.0},
        {"input_rate_per_million": 1.0},
        {"scope": "invalid"},
    ],
)
def test_preview_rejects_invalid_scope_or_rates(client, service, body):
    response = client.post("/categories/reclassify/preview", json=body)

    assert response.status_code == 422
    service.preview_reclassification.assert_not_called()


def test_reclassification_requires_exact_confirmation(client, service):
    response = client.post("/categories/reclassify", json={"scope": "all", "confirm": "yes"})

    assert response.status_code == 422
    service.start_reclassification.assert_not_called()


def test_reclassification_starts_confirmed_scope(client, service):
    response = client.post("/categories/reclassify", json={"scope": "all", "confirm": "RECLASSIFY"})

    assert response.status_code == 202
    service.start_reclassification.assert_called_once_with(scope="all", confirm="RECLASSIFY", owner_id=OWNER_ID)
    assert response.json() == {"created_jobs": 1, "skipped_active_jobs": 1, "eligible_memories": 2}
