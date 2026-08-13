"""Account-bound authorization contracts for the core memory routes."""

# ruff: noqa: E402 -- server initialization reads these test-only environment values at import time.

import os
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")

from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("AUTH_DISABLED", "true")
os.environ.setdefault("HISTORY_DB_PATH", "/private/tmp/ram0-memory-authorization-history.db")
os.environ.setdefault("OPENAI_API_KEY", "test-key")

import auth
from auth import create_access_token, verify_auth
from db import Base
import memory_authorization
from models import APIKey, User

auth.JWT_SECRET = "test-jwt-secret"

import main


class _SqliteSession(Session):
    """Mirror PostgreSQL UUID coercion for JWT subject lookups in route tests."""

    def get(self, entity, ident, **kwargs):
        if entity is User and isinstance(ident, str):
            ident = uuid.UUID(ident)
        return super().get(entity, ident, **kwargs)


@pytest.fixture
def jwt_account() -> User:
    return User(
        id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        name="JWT account",
        email="jwt@example.com",
        password_hash="unused",
        role="member",
    )


@pytest.fixture
def memory() -> MagicMock:
    memory = MagicMock()
    memory.add.return_value = {"results": []}
    memory.get.return_value = {"id": "memory-id", "memory": "private"}
    memory.get_all.return_value = {"results": []}
    memory.search.return_value = {"results": []}
    memory.update.return_value = {"id": "memory-id", "memory": "updated"}
    memory.history.return_value = []
    return memory


@pytest.fixture
def route_client(jwt_account: User, memory: MagicMock, monkeypatch: pytest.MonkeyPatch):
    api_key_account = User(
        id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
        name="API key account",
        email="api-key@example.com",
        password_hash="unused",
        role="member",
    )
    category_service = MagicMock()
    category_service.resolve_catalog.return_value = None
    category_service.after_add.side_effect = lambda response, _catalog, **_kwargs: response

    def authenticated_account(request: Request) -> User:
        if request.headers.get("X-API-Key") == "account-api-key":
            return api_key_account
        if request.headers.get("Authorization") == "Bearer account-jwt":
            return jwt_account
        raise HTTPException(status_code=401, detail="Authentication required.")

    main.app.dependency_overrides[verify_auth] = authenticated_account
    original_memory = main.get_memory_instance
    original_categories = main.get_category_service
    original_should_log_request = main._should_log_request
    main.get_memory_instance = lambda: memory
    main.get_category_service = lambda: category_service
    main._should_log_request = lambda _request: False
    monkeypatch.setattr(memory_authorization, "require_ownership_ready", lambda: None)
    try:
        yield TestClient(main.app, raise_server_exceptions=False), memory, jwt_account, api_key_account
    finally:
        main.app.dependency_overrides.clear()
        main.get_memory_instance = original_memory
        main.get_category_service = original_categories
        main._should_log_request = original_should_log_request


@pytest.fixture
def rest_auth_client(memory: MagicMock, monkeypatch: pytest.MonkeyPatch):
    """Run the real REST authentication dependency against two account-owned API keys."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[User.__table__, APIKey.__table__])
    sessions = sessionmaker(bind=engine, class_=_SqliteSession, expire_on_commit=False)
    first_owner = User(
        id=uuid.UUID("00000000-0000-0000-0000-000000000011"),
        name="First API key owner",
        email="first-key@example.com",
        password_hash="unused",
        role="member",
    )
    second_owner = User(
        id=uuid.UUID("00000000-0000-0000-0000-000000000022"),
        name="Second API key owner",
        email="second-key@example.com",
        password_hash="unused",
        role="member",
    )
    first_key = "m0sk_first_rest_bearer_key"
    second_key = "m0sk_second_rest_bearer_key"
    with sessions() as session:
        session.add_all(
            [
                first_owner,
                second_owner,
                APIKey(
                    key_prefix=first_key[:12],
                    key_hash=auth.pwd_context.hash(first_key),
                    label="first REST key",
                    created_by=first_owner.id,
                ),
                APIKey(
                    key_prefix=second_key[:12],
                    key_hash=auth.pwd_context.hash(second_key),
                    label="second REST key",
                    created_by=second_owner.id,
                ),
            ]
        )
        session.commit()

    monkeypatch.setattr(auth, "SessionLocal", sessions)
    monkeypatch.setattr(auth, "JWT_SECRET", "rest-auth-test-secret")
    monkeypatch.setattr(memory_authorization, "require_ownership_ready", lambda: None)
    monkeypatch.setattr(main, "get_memory_instance", lambda: memory)
    monkeypatch.setattr(main, "_should_log_request", lambda _request: False)
    try:
        yield {
            "client": TestClient(main.app, raise_server_exceptions=False),
            "memory": memory,
            "sessions": sessions,
            "first_owner": first_owner,
            "second_owner": second_owner,
            "first_key": first_key,
            "second_key": second_key,
            "jwt": create_access_token(str(first_owner.id), first_owner.role),
        }
    finally:
        engine.dispose()


def owner_row(owner_id: str):
    return SimpleNamespace(id="memory-id", payload={"user_id": owner_id, "data": "private"})


@pytest.mark.parametrize(
    "body",
    [
        {"user_id": "00000000-0000-0000-0000-000000000099"},
        {"metadata": {"nested": {"user_id": "00000000-0000-0000-0000-000000000099"}}},
    ],
)
def test_add_rejects_a_caller_supplied_owner(route_client, body: dict[str, object]):
    """Removing the ownership guard must let another account be written as the owner."""
    client, memory, _, _ = route_client
    response = client.post(
        "/memories",
        json={
            "messages": [{"role": "user", "content": "private"}],
            **body,
        },
        headers={"Authorization": "Bearer account-jwt"},
    )

    assert response.status_code == 422
    memory.add.assert_not_called()


@pytest.mark.parametrize(
    ("headers", "expected_owner"),
    [
        ({"Authorization": "Bearer account-jwt"}, "00000000-0000-0000-0000-000000000001"),
        ({"X-API-Key": "account-api-key"}, "00000000-0000-0000-0000-000000000002"),
    ],
)
def test_add_derives_owner_from_the_authenticated_principal(route_client, headers: dict[str, str], expected_owner: str):
    """Replacing the principal with request fields would write one account's memory for another."""
    client, memory, _, _ = route_client

    response = client.post(
        "/memories",
        json={"messages": [{"role": "user", "content": "private"}], "agent_id": "assistant"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert memory.add.call_args.kwargs["user_id"] == expected_owner
    assert "00000000-0000-0000-0000-000000000099" not in memory.add.call_args.kwargs.values()


def test_create_passes_valid_app_id_beneath_derived_owner(route_client):
    """Dropping a trusted app_id would store a project decision in the account-wide scope."""
    client, memory, jwt_account, _ = route_client

    response = client.post(
        "/memories",
        headers={"Authorization": "Bearer account-jwt"},
        json={
            "messages": [{"role": "user", "content": "Decision: Use pgvector."}],
            "app_id": "github.com-olhapi-ram0",
        },
    )

    assert response.status_code == 200, response.text
    assert memory.add.call_args.kwargs["user_id"] == str(jwt_account.id)
    assert memory.add.call_args.kwargs["app_id"] == "github.com-olhapi-ram0"


@pytest.mark.parametrize(
    "body",
    [
        {"app_id": "../checkout"},
        {"metadata": {"app_id": "github.com-olhapi-other"}},
    ],
)
def test_create_rejects_invalid_or_metadata_app_id(route_client, body):
    """Accepting an invalid or metadata app selector would bypass the trusted top-level scope."""
    client, memory, _, _ = route_client

    response = client.post(
        "/memories",
        headers={"Authorization": "Bearer account-jwt"},
        json={"messages": [{"role": "user", "content": "private"}], **body},
    )

    assert response.status_code == 422
    memory.add.assert_not_called()


def test_list_and_search_are_scoped_to_the_authenticated_owner(route_client):
    """Removing the owner filter exposes memories belonging to every account."""
    client, memory, jwt_account, _ = route_client

    listed = client.get(
        "/memories",
        params=[("agent_id", "assistant"), ("categories", "work")],
        headers={"Authorization": "Bearer account-jwt"},
    )
    searched = client.post(
        "/search",
        json={
            "query": "private",
            "agent_id": "assistant",
            "run_id": "run-1",
            "filters": {"categories": {"in": ["work"]}},
        },
        headers={"Authorization": "Bearer account-jwt"},
    )

    assert listed.status_code == 200, listed.text
    assert searched.status_code == 200, searched.text
    assert memory.get_all.call_args.kwargs["filters"] == {
        "user_id": str(jwt_account.id),
        "agent_id": "assistant",
        "categories": {"in": ["work"]},
    }
    assert memory.search.call_args.kwargs["filters"] == {
        "user_id": str(jwt_account.id),
        "agent_id": "assistant",
        "run_id": "run-1",
        "categories": {"in": ["work"]},
    }


def test_search_with_app_id_never_loses_owner_filter(route_client):
    """Applying app scope without the owner would expose another account using the same app label."""
    client, memory, jwt_account, _ = route_client

    response = client.post(
        "/search",
        headers={"Authorization": "Bearer account-jwt"},
        json={"query": "pgvector", "filters": {"app_id": "github.com-olhapi-ram0"}},
    )

    assert response.status_code == 200, response.text
    assert memory.search.call_args.kwargs["filters"] == {
        "user_id": str(jwt_account.id),
        "app_id": "github.com-olhapi-ram0",
    }


def test_list_and_delete_all_compose_app_id_with_owner(route_client):
    """An app-only list or delete would cross account boundaries for shared project labels."""
    client, memory, jwt_account, _ = route_client
    headers = {"Authorization": "Bearer account-jwt"}

    listed = client.get("/memories", headers=headers, params={"app_id": "github.com-olhapi-ram0"})
    deleted = client.delete("/memories", headers=headers, params={"app_id": "github.com-olhapi-ram0"})

    assert listed.status_code == 200, listed.text
    assert deleted.status_code == 200, deleted.text
    assert memory.get_all.call_args.kwargs["filters"] == {
        "user_id": str(jwt_account.id),
        "app_id": "github.com-olhapi-ram0",
    }
    memory.delete_all.assert_called_once_with(
        user_id=str(jwt_account.id),
        agent_id=None,
        run_id=None,
        app_id="github.com-olhapi-ram0",
    )


def test_search_accepts_matching_top_level_and_filter_app_id(route_client):
    """Equivalent transports should compose one trusted project selector beneath the owner."""
    client, memory, _, _ = route_client

    response = client.post(
        "/search",
        headers={"Authorization": "Bearer account-jwt"},
        json={
            "query": "private",
            "app_id": "github.com-olhapi-ram0",
            "filters": {"app_id": "github.com-olhapi-ram0"},
        },
    )

    assert response.status_code == 200, response.text
    assert memory.search.call_args.kwargs["filters"]["app_id"] == "github.com-olhapi-ram0"


def test_search_rejects_conflicting_top_level_and_filter_app_id(route_client):
    """Conflicting project selectors must not let one transport override the other."""
    client, memory, _, _ = route_client

    response = client.post(
        "/search",
        headers={"Authorization": "Bearer account-jwt"},
        json={
            "query": "private",
            "app_id": "github.com-olhapi-ram0",
            "filters": {"app_id": "github.com-olhapi-other"},
        },
    )

    assert response.status_code == 422
    memory.search.assert_not_called()


def test_app_id_is_preserved_in_collection_responses(route_client):
    """Dropping app_id from responses would prevent clients from distinguishing project scope."""
    client, memory, _, _ = route_client
    memory.get_all.return_value = {
        "results": [{"id": "memory-id", "memory": "private", "app_id": "github.com-olhapi-ram0"}]
    }
    memory.search.return_value = memory.get_all.return_value
    headers = {"Authorization": "Bearer account-jwt"}

    listed = client.get("/memories", headers=headers)
    searched = client.post("/search", headers=headers, json={"query": "private"})

    assert listed.json()["results"][0]["app_id"] == "github.com-olhapi-ram0"
    assert searched.json()["results"][0]["app_id"] == "github.com-olhapi-ram0"


def test_two_accounts_can_share_an_app_label_without_sharing_owner_scope(route_client):
    """The same Git project label is account-local and must retain a distinct owner filter."""
    client, memory, jwt_account, api_key_account = route_client
    body = {"query": "private", "app_id": "github.com-olhapi-ram0"}

    first = client.post("/search", headers={"Authorization": "Bearer account-jwt"}, json=body)
    second = client.post("/search", headers={"X-API-Key": "account-api-key"}, json=body)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert [call.kwargs["filters"] for call in memory.search.call_args_list] == [
        {"user_id": str(jwt_account.id), "app_id": "github.com-olhapi-ram0"},
        {"user_id": str(api_key_account.id), "app_id": "github.com-olhapi-ram0"},
    ]


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", "/memories?user_id=00000000-0000-0000-0000-000000000099", None),
        (
            "post",
            "/search",
            {"query": "private", "filters": {"AND": [{"user_id": "00000000-0000-0000-0000-000000000099"}]}},
        ),
        ("delete", "/memories?user_id=00000000-0000-0000-0000-000000000099", None),
    ],
)
def test_collection_routes_reject_client_owner_selectors(route_client, method: str, path: str, body: dict | None):
    """Accepting a user_id in any collection route turns its filter into a data-exfiltration capability."""
    client, memory, _, _ = route_client
    response = client.request(method, path, json=body, headers={"Authorization": "Bearer account-jwt"})

    assert response.status_code == 422
    memory.get_all.assert_not_called()
    memory.search.assert_not_called()
    memory.delete_all.assert_not_called()


def test_search_rejects_top_level_user_id(route_client):
    """Accepting the deprecated top-level owner selector lets a caller search another account."""
    client, memory, _, _ = route_client

    response = client.post(
        "/search",
        json={"query": "private", "user_id": "00000000-0000-0000-0000-000000000099"},
        headers={"Authorization": "Bearer account-jwt"},
    )

    assert response.status_code == 422
    memory.search.assert_not_called()


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", "/memories/memory-id", None),
        ("put", "/memories/memory-id", {"text": "changed"}),
        ("get", "/memories/memory-id/history", None),
        ("delete", "/memories/memory-id", None),
    ],
)
def test_direct_operations_hide_foreign_memory_ids(route_client, method: str, path: str, body: dict | None):
    """Skipping the vector-row owner check lets a caller read or mutate a guessed foreign ID."""
    client, memory, _, _ = route_client
    memory.vector_store.get.return_value = owner_row("00000000-0000-0000-0000-000000000099")

    response = client.request(method, path, json=body, headers={"X-API-Key": "account-api-key"})

    assert response.json() == {"detail": "Memory not found."}
    assert response.status_code == 404
    memory.get.assert_not_called()
    memory.update.assert_not_called()
    memory.history.assert_not_called()
    memory.delete.assert_not_called()


def test_update_rejects_owner_in_metadata(route_client):
    """Allowing metadata user_id lets an update replace the row's ownership field."""
    client, memory, jwt_account, _ = route_client
    memory.vector_store.get.return_value = owner_row(str(jwt_account.id))

    response = client.put(
        "/memories/memory-id",
        json={"metadata": {"user_id": "00000000-0000-0000-0000-000000000099"}},
        headers={"Authorization": "Bearer account-jwt"},
    )

    assert response.status_code == 422
    memory.update.assert_not_called()


def test_bulk_delete_and_reset_only_target_the_authenticated_owner(route_client):
    """Calling reset or bulk delete without an account owner erases other accounts' memories."""
    client, memory, _, api_key_account = route_client

    deleted = client.delete(
        "/memories",
        params={"agent_id": "assistant", "run_id": "run-1"},
        headers={"X-API-Key": "account-api-key"},
    )
    reset = client.post("/reset", headers={"X-API-Key": "account-api-key"})

    assert deleted.status_code == 200, deleted.text
    assert reset.status_code == 200, reset.text
    assert memory.delete_all.call_args_list[0].kwargs == {
        "user_id": str(api_key_account.id),
        "agent_id": "assistant",
        "run_id": "run-1",
    }
    assert memory.delete_all.call_args_list[1].kwargs == {"user_id": str(api_key_account.id)}
    memory.reset.assert_not_called()


def test_whole_owner_delete_only_targets_the_authenticated_account(route_client):
    """Omitting agent and run scopes must still delete only the caller's account memories."""
    client, memory, jwt_account, _ = route_client

    response = client.delete("/memories", headers={"Authorization": "Bearer account-jwt"})

    assert response.status_code == 200, response.text
    memory.delete_all.assert_called_once_with(user_id=str(jwt_account.id), agent_id=None, run_id=None)


def test_bearer_api_key_scopes_list_and_search_to_its_owner(rest_auth_client):
    """Removing bearer API-key fallback leaves automation clients unable to reach owner-scoped routes."""
    client = rest_auth_client["client"]
    memory = rest_auth_client["memory"]
    first_owner = rest_auth_client["first_owner"]
    headers = {"Authorization": f"Bearer {rest_auth_client['first_key']}"}

    listed = client.get("/memories", headers=headers)
    searched = client.post("/search", json={"query": "private"}, headers=headers)

    assert listed.status_code == 200, listed.text
    assert searched.status_code == 200, searched.text
    assert memory.get_all.call_args.kwargs["filters"]["user_id"] == str(first_owner.id)
    assert memory.search.call_args.kwargs["filters"]["user_id"] == str(first_owner.id)


@pytest.mark.parametrize("state", ["malformed", "revoked"])
def test_bearer_api_key_failures_are_generic_and_unauthenticated(rest_auth_client, state: str):
    """Leaking JWT or API-key lookup details would make invalid REST bearer credentials enumerable."""
    client = rest_auth_client["client"]
    if state == "revoked":
        with rest_auth_client["sessions"]() as session:
            key = session.query(APIKey).filter_by(label="first REST key").one()
            key.revoked_at = datetime.now(timezone.utc)
            session.commit()
        credential = rest_auth_client["first_key"]
    else:
        credential = "malformed-rest-bearer-value"

    response = client.get("/memories", headers={"Authorization": f"Bearer {credential}"})

    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required."}
    assert response.headers["www-authenticate"] == "Bearer"


def test_bearer_api_key_cannot_read_another_owners_memory(rest_auth_client):
    """Resolving a bearer key to the wrong user would expose direct memory IDs across accounts."""
    client = rest_auth_client["client"]
    memory = rest_auth_client["memory"]
    first_owner = rest_auth_client["first_owner"]
    memory.vector_store.get.return_value = owner_row(str(first_owner.id))

    response = client.get(
        "/memories/first-owners-memory",
        headers={"Authorization": f"Bearer {rest_auth_client['second_key']}"},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Memory not found."}
    memory.get.assert_not_called()


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param(
            lambda fixture: {"Authorization": f"Bearer {fixture['jwt']}"},
            id="jwt-bearer",
        ),
        pytest.param(
            lambda fixture: {"X-API-Key": fixture["first_key"]},
            id="legacy-x-api-key",
        ),
    ],
)
def test_jwt_bearer_and_legacy_header_still_authenticate_their_owner(rest_auth_client, headers):
    """Changing bearer fallback must not bypass valid JWTs or the documented X-API-Key transport."""
    client = rest_auth_client["client"]
    memory = rest_auth_client["memory"]

    response = client.get("/memories", headers=headers(rest_auth_client))

    assert response.status_code == 200, response.text
    assert memory.get_all.call_args.kwargs["filters"]["user_id"] == str(rest_auth_client["first_owner"].id)
