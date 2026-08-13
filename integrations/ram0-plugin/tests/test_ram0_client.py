"""Contract tests for the isolated Ram0 REST adapter."""

from __future__ import annotations

import json
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

import pytest

from ram0_client import Ram0Client, Ram0ClientError
from conftest import RecordingRam0Server


def _payload(request: dict[str, Any]) -> Any:
    return json.loads(request["body"]) if request["body"] is not None else None


def _assert_no_owner_or_lifecycle_fields(value: Any) -> None:
    forbidden = {"user_id", "run_id", "expiration_date"}
    if isinstance(value, dict):
        assert not (set(value) & forbidden)
        for child in value.values():
            _assert_no_owner_or_lifecycle_fields(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_owner_or_lifecycle_fields(child)


@pytest.mark.parametrize(
    ("operation", "method", "path", "expected_payload"),
    [
        (
            lambda client: client.search("architecture", limit=7, app_id="app-a", scope="project"),
            "POST",
            "/search",
            {"query": "architecture", "top_k": 7, "scope": "project", "app_id": "app-a"},
        ),
        (
            lambda client: client.add("Prefer tests", {"project": "ram0"}, app_id="app-a"),
            "POST",
            "/memories",
            {
                "messages": [{"role": "user", "content": "Prefer tests"}],
                "metadata": {"project": "ram0"},
                "app_id": "app-a",
            },
        ),
        (
            lambda client: client.add_durable("Prefer tests", {"project": "ram0"}, app_id="app-a"),
            "POST",
            "/memories",
            {
                "messages": [{"role": "user", "content": "Prefer tests"}],
                "metadata": {"project": "ram0"},
                "infer": False,
                "app_id": "app-a",
            },
        ),
        (
            lambda client: client.list(limit=3, app_id="app-a", scope="project"),
            "GET",
            "/memories?top_k=3&scope=project&app_id=app-a",
            None,
        ),
        (lambda client: client.get("memory-1"), "GET", "/memories/memory-1", None),
        (
            lambda client: client.update("memory-1", "Updated", {"branch": "main"}),
            "PUT",
            "/memories/memory-1",
            {"text": "Updated", "metadata": {"branch": "main"}},
        ),
        (lambda client: client.delete("memory-1"), "DELETE", "/memories/memory-1", None),
        (lambda client: client.get_categories(), "GET", "/categories", None),
        (
            lambda client: client.create_category({"name": "architecture", "description": "System design"}),
            "POST",
            "/categories",
            {"name": "architecture", "description": "System design"},
        ),
        (
            lambda client: client.put_categories([{"name": "architecture", "description": "System design"}]),
            "PUT",
            "/categories",
            [{"name": "architecture", "description": "System design"}],
        ),
    ],
)
def test_operations_send_only_bearer_auth_and_safe_payloads(
    ram0_server, operation: Callable[[Ram0Client], Any], method: str, path: str, expected_payload: Any
):
    """Breaks if an operation changes the Ram0 REST route, auth, or payload contract."""
    client = Ram0Client(ram0_server.url, "ram0-test-key")

    operation(client)

    request = ram0_server.requests.pop()
    assert request["method"] == method
    assert request["path"] == path
    assert [value for key, value in request["headers"].items() if key == "authorization"] == ["Bearer ram0-test-key"]
    assert request["headers"]["user-agent"].startswith("ram0-plugin/")
    assert ("content-type" in request["headers"]) is (expected_payload is not None)
    assert _payload(request) == expected_payload
    _assert_no_owner_or_lifecycle_fields(expected_payload)


@pytest.mark.parametrize(
    ("scope", "expected_context"),
    [
        (None, {"app_id": "app-a"}),
        ("project", {"scope": "project", "app_id": "app-a"}),
        ("global", {"scope": "global"}),
    ],
)
def test_search_scope_matrix_sends_only_trusted_scope_intent(ram0_server, scope, expected_context):
    """Breaks if a read widens or narrows the selected project/global scope."""
    client = Ram0Client(ram0_server.url, "ram0-test-key")

    client.search("architecture", app_id="app-a", scope=scope)

    payload = _payload(ram0_server.requests[0])
    assert payload == {
        "query": "architecture",
        "top_k": 10,
        **expected_context,
    }


@pytest.mark.parametrize(
    ("scope", "expected_query"),
    [
        (
            None,
            {"top_k": ["100"], "app_id": ["app-a"]},
        ),
        ("project", {"top_k": ["100"], "scope": ["project"], "app_id": ["app-a"]}),
        ("global", {"top_k": ["100"], "scope": ["global"]}),
    ],
)
def test_list_scope_matrix_sends_only_the_selected_filter(ram0_server, scope, expected_query):
    """Breaks if list scope leaks another project or omits global memories from the default."""
    client = Ram0Client(ram0_server.url, "ram0-test-key")

    client.list(app_id="app-a", scope=scope)

    assert parse_qs(urlsplit(ram0_server.requests[0]["path"]).query) == expected_query


@pytest.mark.parametrize(
    ("operation", "scope", "expected_app_id"),
    [
        ("add", None, "app-a"),
        ("add", "project", "app-a"),
        ("add", "global", None),
        ("add_durable", None, "app-a"),
        ("add_durable", "project", "app-a"),
        ("add_durable", "global", None),
    ],
)
def test_write_scope_matrix_controls_only_trusted_top_level_app_id(ram0_server, operation, scope, expected_app_id):
    """Breaks if project writes become global or global writes acquire a project label."""
    client = Ram0Client(ram0_server.url, "ram0-test-key")

    getattr(client, operation)("Decision", {"kind": "decision"}, app_id="app-a", scope=scope)

    payload = _payload(ram0_server.requests[0])
    assert payload.get("app_id") == expected_app_id
    assert ("app_id" in payload) is (expected_app_id is not None)
    assert "app_id" not in payload["metadata"]


@pytest.mark.parametrize("scope", ["", "all", "repository", 1])
def test_invalid_scope_is_rejected_before_network(ram0_server, scope):
    """Breaks if a typo silently widens a read or changes write placement."""
    client = Ram0Client(ram0_server.url, "ram0-test-key")

    with pytest.raises(ValueError, match="scope"):
        client.search("architecture", app_id="app-a", scope=scope)

    assert ram0_server.requests == []


@pytest.mark.parametrize("operation", ["search", "list", "add", "add_durable"])
def test_default_and_project_scopes_require_normalized_app_id_before_network(ram0_server, operation):
    """Breaks if an absent current project silently becomes account-global."""
    client = Ram0Client(ram0_server.url, "ram0-test-key")
    arguments = ("architecture",) if operation in {"search", "add", "add_durable"} else ()

    with pytest.raises(ValueError, match="app_id"):
        getattr(client, operation)(*arguments, app_id=None)

    assert ram0_server.requests == []


@pytest.mark.parametrize(
    "reserved_key",
    [
        "user_id",
        "app_id",
        "run_id",
        "expiration_date",
        "authorization",
        "api_key",
        "api_token",
        "access_token",
        "refresh_token",
        "client_secret",
        "service_credentials",
        "secret_key",
        "db_password",
        "database_password",
    ],
)
def test_metadata_rejects_server_owned_and_credential_fields_before_network(ram0_server, reserved_key: str):
    """Breaks if unsafe metadata can cross the account-derived ownership seam."""
    client = Ram0Client(ram0_server.url, "ram0-test-key")

    with pytest.raises(ValueError, match="reserved"):
        client.add("Never send owner data", {reserved_key: "value"}, app_id="app-a")

    assert ram0_server.requests == []


@pytest.mark.parametrize(
    "reserved_key",
    ["user_id", "app_id", "run_id", "expiration_date", "api_token", "access_token", "secret_key", "db_password"],
)
def test_category_payloads_reject_reserved_keys_nested_in_tuples_before_network(ram0_server, reserved_key: str):
    """Breaks if non-metadata JSON payloads or tuple children bypass reserved-key validation."""
    client = Ram0Client(ram0_server.url, "ram0-test-key")
    definitions = (
        {
            "name": "architecture",
            "description": "System design",
            "extension": ({"nested": {reserved_key: "value"}},),
        },
    )

    with pytest.raises(ValueError, match="reserved"):
        client.put_categories(definitions)

    assert ram0_server.requests == []


@pytest.mark.parametrize("padded_key", ["\tUser-ID ", " expiration_date\n", " API-TOKEN "])
def test_memory_metadata_rejects_padded_reserved_keys_before_network(ram0_server, padded_key: str):
    """Breaks if whitespace around identity, expiry, or credential keys bypasses metadata validation."""
    client = Ram0Client(ram0_server.url, "ram0-test-key")

    with pytest.raises(ValueError, match="reserved"):
        client.add("Never send padded reserved fields", {padded_key: "value"}, app_id="app-a")

    assert ram0_server.requests == []


@pytest.mark.parametrize("padded_key", ["\tUser-ID ", " expiration_date\n", " API-TOKEN "])
def test_category_payloads_reject_padded_reserved_keys_before_network(ram0_server, padded_key: str):
    """Breaks if whitespace around identity, expiry, or credential keys bypasses category validation."""
    client = Ram0Client(ram0_server.url, "ram0-test-key")

    with pytest.raises(ValueError, match="reserved"):
        client.put_categories([{"name": "architecture", "description": "System design", padded_key: "value"}])

    assert ram0_server.requests == []


def test_credential_adjacent_metadata_keys_are_not_false_positives(ram0_server):
    """Breaks if reserved-key detection broadly rejects harmless names containing credential-like words."""
    client = Ram0Client(ram0_server.url, "ram0-test-key")

    client.add(
        "Keep harmless metadata",
        {
            "token_budget": 100,
            "secretary": "Ada",
            "password_policy": "rotated",
            "credential_rotation_date": "2026-08-10",
        },
        app_id="app-a",
    )

    assert len(ram0_server.requests) == 1


def test_client_strips_api_key_whitespace_before_bearer_auth(ram0_server):
    """Breaks if shell formatting whitespace becomes part of the bearer credential."""
    client = Ram0Client(ram0_server.url, "  ram0-test-key\t")

    client.get_categories()

    assert ram0_server.requests[0]["headers"]["authorization"] == "Bearer ram0-test-key"


@pytest.mark.parametrize("api_key", ["", " ", "\t\n"])
def test_client_rejects_blank_api_keys(api_key: str):
    """Breaks if blank credentials can enable or attempt network operations."""
    with pytest.raises(ValueError, match="RAM0_API_KEY"):
        Ram0Client("http://localhost:8888", api_key)


def test_http_errors_are_actionable_without_leaking_credentials_or_payload(ram0_server):
    """Breaks if errors expose secrets or request content instead of actionable status guidance."""
    ram0_server.status = 503
    ram0_server.response = {"detail": "ram0-test-key: Prefer tests"}
    client = Ram0Client(ram0_server.url, "ram0-test-key")

    with pytest.raises(Ram0ClientError) as raised:
        client.add("Prefer tests", {"project": "ram0"}, app_id="app-a")

    error = raised.value
    assert (error.status, error.code, error.action) == (503, "service_unavailable", "Check RAM0_API_URL and try again.")
    assert "ram0-test-key" not in str(error)
    assert "Prefer tests" not in str(error)


def test_client_does_not_forward_bearer_authorization_across_redirects(ram0_server):
    """Breaks if urllib follows an attacker-controlled redirect with the Ram0 bearer credential."""
    with RecordingRam0Server() as target:
        ram0_server.status = 302
        ram0_server.response_headers = {"Location": f"{target.url}stolen"}
        client = Ram0Client(ram0_server.url, "ram0-test-key")

        with pytest.raises(Ram0ClientError) as raised:
            client.get_categories()

        assert raised.value.code == "request_rejected"
        assert target.requests == []
