"""Live two-account acceptance test for the Ram0 automation plugin."""

from __future__ import annotations

import io
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_SCRIPTS = REPOSITORY_ROOT / "integrations" / "ram0-plugin" / "scripts"
SERVER_SCRIPTS = REPOSITORY_ROOT / "server" / "scripts"
sys.path.insert(0, str(PLUGIN_SCRIPTS))
sys.path.insert(0, str(SERVER_SCRIPTS))

import e2e_ram0_plugin as e2e_harness  # noqa: E402
import ram0_client as ram0_client_module  # noqa: E402
from mcp_stdio_adapter import run_stdio  # noqa: E402
from memory_capture import capture_durable, inject_search_context  # noqa: E402
from ram0_client import Ram0Client, Ram0ClientError  # noqa: E402
from ram0_config import write_config  # noqa: E402
from setup_coding_categories import CODING_CATEGORIES, onboard_categories  # noqa: E402


PRIVATE_CATEGORY_A = {"name": "owner_a_private", "description": "Only account A may manage this catalog."}
PRIVATE_CATEGORY_B = {"name": "owner_b_private", "description": "Only account B may manage this catalog."}
CAPTURE_CREDENTIAL = "sk-abcdefghijklmnopqrstuvwxyz012345"
CAPTURE_IDENTITY = "owner-a-private@example.com"


def _live_url() -> str:
    value = os.environ.get("RAM0_E2E_API_URL", "").rstrip("/")
    if not value:
        pytest.skip("run through `make -C server e2e-ram0-plugin` for the isolated Docker fixture")
    return value


def _request_json(
    base_url: str,
    method: str,
    path: str,
    *,
    body: Any = None,
    bearer: str | None = None,
    expected: int,
) -> Any:
    encoded = json.dumps(body, separators=(",", ":")).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if encoded is not None else {}
    if bearer is not None:
        headers["Authorization"] = f"Bearer {bearer}"
    request = Request(f"{base_url}{path}", data=encoded, headers=headers, method=method)
    try:
        with urlopen(request, timeout=30) as response:
            status = response.status
            payload = response.read()
    except HTTPError as error:
        status = error.code
        payload = error.read()
    assert status == expected, f"{method} {path}: HTTP {status}: {payload[:500]!r}"
    return json.loads(payload) if payload else {}


def _create_accounts_and_keys(base_url: str) -> tuple[str, str, str]:
    registered = _request_json(
        base_url,
        "POST",
        "/auth/register",
        body={"name": "Plugin Owner A", "email": "plugin-owner-a@example.com", "password": "test-owner-a-pass"},
        expected=200,
    )
    admin_jwt = registered["access_token"]
    invitation = _request_json(
        base_url,
        "POST",
        "/admin/invitations",
        body={"email": "plugin-owner-b@example.com"},
        bearer=admin_jwt,
        expected=201,
    )
    invitation_token = invitation["invite_url"].split("#token=", 1)[1]
    accepted = _request_json(
        base_url,
        "POST",
        "/auth/invitations/accept",
        body={"token": invitation_token, "password": "test-owner-b-pass"},
        expected=200,
    )
    member_jwt = accepted["access_token"]
    owner_a_key = _request_json(
        base_url,
        "POST",
        "/api-keys",
        body={"label": "plugin-e2e-owner-a"},
        bearer=admin_jwt,
        expected=201,
    )["key"]
    owner_b_key = _request_json(
        base_url,
        "POST",
        "/api-keys",
        body={"label": "plugin-e2e-owner-b"},
        bearer=member_jwt,
        expected=201,
    )["key"]
    return admin_jwt, owner_a_key, owner_b_key


def _results(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict) and isinstance(value.get("results"), list):
        return [item for item in value["results"] if isinstance(item, dict)]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _catalog_names(value: Any, field: str = "saved") -> set[str]:
    definitions = value.get(field, []) if isinstance(value, dict) else []
    return {
        definition["name"]
        for definition in definitions
        if isinstance(definition, dict) and isinstance(definition.get("name"), str)
    }


def _assert_not_found(operation) -> None:
    with pytest.raises(Ram0ClientError) as raised:
        operation()
    assert (raised.value.status, raised.value.code) == (404, "not_found")


def _assert_no_forbidden_keys(value: Any) -> None:
    forbidden = {
        "user_id",
        "app_id",
        "run_id",
        "expiration_date",
        "expires_at",
        "api_key",
        "access_token",
        "authorization",
        "credentials",
    }
    if isinstance(value, dict):
        assert not (set(value) & forbidden)
        for child in value.values():
            _assert_no_forbidden_keys(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_forbidden_keys(child)


def _analytics_rows(since: datetime, minimum: int) -> list[dict[str, Any]]:
    database_url = os.environ.get("RAM0_E2E_DATABASE_URL", "")
    assert database_url, "the Docker harness must expose only its isolated PostgreSQL service"
    import psycopg

    timestamp = since.astimezone(timezone.utc).isoformat()
    query = (
        "SELECT COALESCE(json_agg(row_to_json(request_logs) ORDER BY created_at), '[]'::json) "
        "FROM request_logs "
        "WHERE created_at >= %s::timestamptz"
    )
    deadline = time.monotonic() + 20
    rows: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
            cursor.execute(query, (timestamp,))
            rows = cursor.fetchone()[0]
        if len(rows) >= minimum:
            return rows
        time.sleep(0.1)
    raise AssertionError(f"only {len(rows)} of {minimum} live request analytics rows were persisted")


def test_harness_uses_built_images_without_ports_on_an_internal_network(tmp_path):
    """Breaks if the e2e can pull at runtime, reuse latest code, publish ports, or reach external services."""
    images = e2e_harness._image_names("a" * 16)
    config = e2e_harness._compose_config(images, tmp_path)

    assert all(":latest" not in image for image in images.values())
    assert config["networks"] == {"default": {"internal": True}}
    assert set(config["services"]) == {"postgres", "openai-stub", "ram0-api", "e2e-runner"}
    for service in config["services"].values():
        assert service["pull_policy"] == "never"
        assert "ports" not in service
    assert config["services"]["postgres"]["image"] == e2e_harness.PINNED_PGVECTOR_IMAGE
    assert config["services"]["ram0-api"]["image"] == images["api"]
    assert config["services"]["openai-stub"]["image"] == images["stub"]
    assert config["services"]["e2e-runner"]["image"] == images["runner"]
    runner_environment = config["services"]["e2e-runner"]["environment"]
    assert runner_environment["RAM0_E2E_API_URL"] == "http://ram0-api:8000"
    assert runner_environment["RAM0_E2E_DATABASE_URL"].endswith("@postgres:5432/mem0_app")


def test_offline_harness_never_builds_or_pulls(monkeypatch, tmp_path):
    """The repeatable E2E target may inspect local images but must never contact a registry or build."""
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs) -> str:
        commands.append(command)
        if command[:3] == ["docker", "image", "inspect"] and "--format" in command:
            return "a" * 64
        if command[:3] == ["docker", "network", "inspect"]:
            return "true"
        if command[:2] == ["docker", "inspect"] and "PortBindings" in " ".join(command):
            return "null"
        if command[:2] == ["docker", "inspect"]:
            return "ram0-plugin-e2e-test_default"
        if "ps" in command and "-q" in command:
            return "container-id"
        return ""

    monkeypatch.setattr(e2e_harness, "_run", fake_run)
    images = e2e_harness._image_names("a" * 64)
    e2e_harness._require_prepared_images(images, "a" * 64)
    e2e_harness._run_offline_stack(images, tmp_path, "ram0-plugin-e2e-test")

    assert commands
    assert not any(command[:2] in (["docker", "build"], ["docker", "pull"]) for command in commands)
    compose_up = next(command for command in commands if "up" in command)
    assert "--no-build" in compose_up
    assert compose_up[compose_up.index("--pull") + 1] == "never"


def test_live_plugin_adapter_isolates_accounts_and_keeps_requests_private(monkeypatch, tmp_path):
    """Breaks on identity injection, cross-owner access, unsafe capture, or content-bearing analytics."""
    base_url = _live_url()
    admin_jwt, owner_a_key, owner_b_key = _create_accounts_and_keys(base_url)
    del admin_jwt

    config_home = tmp_path / "config-home"
    write_config(base_url, owner_a_key, home=config_home)
    mcp_input = "\n".join(
        json.dumps(message)
        for message in (
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "ram0-e2e", "version": "1"},
                },
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
    )
    mcp_stdout, mcp_stderr = io.StringIO(), io.StringIO()
    assert run_stdio(io.StringIO(mcp_input + "\n"), mcp_stdout, mcp_stderr, environment={}, home=config_home) == 0
    mcp_messages = [json.loads(line) for line in mcp_stdout.getvalue().splitlines()]
    tools_response = next(
        (message for message in mcp_messages if message.get("id") == 2),
        None,
    )
    assert tools_response is not None, {"stdout": mcp_stdout.getvalue(), "stderr": mcp_stderr.getvalue()}
    assert {tool["name"] for tool in tools_response["result"]["tools"]} == {
        "remember",
        "search_memories",
        "list_memories",
        "get_memory",
        "update_memory",
        "forget_memory",
    }
    assert owner_a_key not in mcp_stdout.getvalue() + mcp_stderr.getvalue()

    client_a = Ram0Client(base_url, owner_a_key, timeout=30)
    client_b = Ram0Client(base_url, owner_b_key, timeout=30)

    captured_requests: list[dict[str, Any]] = []
    real_open_request = ram0_client_module._open_request

    def recording_open_request(request: Request, *, timeout: float):
        captured_requests.append(
            {
                "method": request.get_method(),
                "url": request.full_url,
                "headers": {key.lower(): value for key, value in request.header_items()},
                "body": json.loads(request.data) if request.data is not None else None,
            }
        )
        return real_open_request(request, timeout=timeout)

    monkeypatch.setattr(ram0_client_module, "_open_request", recording_open_request)
    analytics_start = datetime.now(timezone.utc)

    client_a.put_categories([PRIVATE_CATEGORY_A])
    assert (
        capture_durable(
            "Architecture: The live Ram0 adapter remains account-scoped.",
            client_a,
            state_dir=tmp_path / "seed",
            source="e2e",
            scope="owner-a",
            proof_key=owner_a_key,
        )
        == 1
    )
    seeded_results = _results(client_a.list(limit=100))
    assert len(seeded_results) == 1 and isinstance(seeded_results[0].get("id"), str)
    owner_a_memory_id = seeded_results[0]["id"]

    retrieved = inject_search_context(
        "architecture authorization",
        client_a,
        purpose="session",
        sensitive_values=(owner_a_key,),
        proof_key=owner_a_key,
    )
    assert retrieved.startswith("<ram0-memory-context>")
    assert "Relevant durable memories" in retrieved

    owner_a_before_capture_ids = {item["id"] for item in _results(client_a.list(limit=100))}
    assert owner_a_before_capture_ids == {owner_a_memory_id}
    captured = capture_durable(
        (
            "Decision: The account boundary excludes "
            f"{CAPTURE_CREDENTIAL}, native key {owner_a_key}, and configured key {owner_a_key} "
            f"for {CAPTURE_IDENTITY} before durable storage."
        ),
        client_a,
        state_dir=tmp_path / "capture",
        source="e2e",
        scope="owner-a",
        sensitive_values=(owner_a_key,),
        proof_key=owner_a_key,
    )
    assert captured == 1
    owner_a_after_capture = _results(client_a.list(limit=100))
    owner_a_after_capture_ids = {item["id"] for item in owner_a_after_capture}
    captured_ids = owner_a_after_capture_ids - owner_a_before_capture_ids
    assert len(captured_ids) == 1
    captured_memory_id = captured_ids.pop()
    assert captured_memory_id != owner_a_memory_id
    persisted_capture = client_a.get(captured_memory_id)
    persisted_text = persisted_capture["memory"]
    assert "[redacted credential]" in persisted_text
    assert "[redacted identity]" in persisted_text
    assert CAPTURE_CREDENTIAL not in persisted_text
    assert CAPTURE_IDENTITY not in persisted_text
    assert owner_a_key.startswith("m0sk_")
    assert owner_a_key not in persisted_text
    assert onboard_categories(client_a, data_dir=tmp_path / "categories", marker_scope="owner-a") is True

    owner_a_catalog = client_a.get_categories()
    owner_a_saved = _catalog_names(owner_a_catalog)
    assert PRIVATE_CATEGORY_A["name"] in owner_a_saved
    assert {definition["name"] for definition in CODING_CATEGORIES} <= owner_a_saved
    assert _results(client_a.list(limit=100))

    assert _results(client_b.list(limit=100)) == []
    assert _results(client_b.search("architecture authorization", limit=100)) == []
    _assert_not_found(lambda: client_b.get(owner_a_memory_id))
    _assert_not_found(lambda: client_b.update(owner_a_memory_id, "foreign overwrite", {"source": "forbidden"}))
    _assert_not_found(lambda: client_b.delete(owner_a_memory_id))
    assert client_a.get(owner_a_memory_id)["id"] == owner_a_memory_id

    owner_b_initial = client_b.get_categories()
    assert PRIVATE_CATEGORY_A["name"] not in _catalog_names(owner_b_initial, "saved")
    assert PRIVATE_CATEGORY_A["name"] not in _catalog_names(owner_b_initial, "active")
    client_b.put_categories([PRIVATE_CATEGORY_B])
    owner_b_catalog = client_b.get_categories()
    assert _catalog_names(owner_b_catalog) == {PRIVATE_CATEGORY_B["name"]}
    assert PRIVATE_CATEGORY_A["name"] not in _catalog_names(owner_b_catalog, "active")
    owner_a_catalog_after_b_write = client_a.get_categories()
    assert _catalog_names(owner_a_catalog_after_b_write) == owner_a_saved
    assert PRIVATE_CATEGORY_B["name"] not in _catalog_names(owner_a_catalog_after_b_write, "active")

    assert captured_requests
    for captured_request in captured_requests:
        headers = captured_request["headers"]
        assert set(headers) <= {"authorization", "content-type"}
        assert headers.get("authorization") in {f"Bearer {owner_a_key}", f"Bearer {owner_b_key}"}
        assert "x-api-key" not in headers
        assert owner_a_key not in captured_request["url"] and owner_b_key not in captured_request["url"]
        _assert_no_forbidden_keys(captured_request["body"])

    request_bodies = json.dumps([item["body"] for item in captured_requests], separators=(",", ":"))
    assert owner_a_key not in request_bodies and owner_b_key not in request_bodies
    assert CAPTURE_CREDENTIAL not in request_bodies
    assert CAPTURE_IDENTITY not in request_bodies
    assert "[redacted credential]" in request_bodies
    assert "[redacted identity]" in request_bodies

    analytics = _analytics_rows(analytics_start, len(captured_requests))
    assert analytics
    for event in analytics:
        assert set(event) == {"id", "method", "path", "status_code", "latency_ms", "auth_type", "created_at"}
        assert event["method"] in {"GET", "POST", "PUT", "DELETE"}
        assert isinstance(event["id"], str) and event["id"]
        assert isinstance(event["status_code"], int)
        assert isinstance(event["latency_ms"], (int, float)) and event["latency_ms"] >= 0
        expected_auth_type = "mcp_bearer_api_key" if event["path"].rstrip("/") == "/mcp" else "bearer"
        assert event["auth_type"] == expected_auth_type
    analytics_json = json.dumps(analytics, separators=(",", ":"))
    for private_value in (
        owner_a_key,
        owner_b_key,
        CAPTURE_CREDENTIAL,
        CAPTURE_IDENTITY,
        PRIVATE_CATEGORY_A["description"],
        PRIVATE_CATEGORY_B["description"],
        "foreign overwrite",
    ):
        assert private_value not in analytics_json
