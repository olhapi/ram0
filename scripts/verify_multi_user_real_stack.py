#!/usr/bin/env python3
"""Disposable real-stack proof for Ram0 multi-user ownership.

The script starts a fresh pgvector PostgreSQL container, runs the real Alembic
migrations and FastAPI application over a loopback HTTP socket, and stubs only
the external embedding call. It prints a secret-free PASS matrix and removes
the container, database dumps, restored databases, and generated history DB.
"""

# Modified for Ram0; see NOTICE and repository history.

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import psycopg
import uvicorn


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server"
CONTAINER = f"ram0-task7-r3-pg-{os.getpid()}"
PRE_VECTOR_DUMP = "/tmp/ram0-task7-r3-pre-vector.dump"
PRE_APP_DUMP = "/tmp/ram0-task7-r3-pre-app.dump"
POST_VECTOR_DUMP = "/tmp/ram0-task7-r3-post-vector.dump"
POST_APP_DUMP = "/tmp/ram0-task7-r3-post-app.dump"


def run(*args: str, cwd: Path | None = None, env: dict[str, str] | None = None, capture: bool = False) -> str:
    completed = subprocess.run(
        args,
        cwd=cwd,
        env=env,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.PIPE if capture else subprocess.DEVNULL,
    )
    return completed.stdout.strip() if capture else ""


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_postgres(db_port: int) -> None:
    for _ in range(100):
        try:
            run("docker", "exec", CONTAINER, "psql", "-U", "postgres", "-d", "postgres", "-tAc", "SELECT 1")
            with psycopg.connect(
                host="127.0.0.1", port=db_port, user="postgres", password="", dbname="postgres", connect_timeout=1
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    if cursor.fetchone() == (1,):
                        return
        except (OSError, psycopg.Error, subprocess.CalledProcessError):
            time.sleep(0.1)
    raise RuntimeError("PostgreSQL did not become ready")


def assert_status(response: httpx.Response, expected: int, label: str) -> None:
    if response.status_code != expected:
        raise AssertionError(f"{label}: expected HTTP {expected}, got {response.status_code}")


def ids(payload: dict[str, Any]) -> set[str]:
    return {str(item["id"]) for item in payload.get("results", [])}


def mcp_memory_ids(result: Any, envelope_key: str) -> set[str]:
    """Extract IDs from the actual remember/list tool_success envelopes."""
    payload = result.structured_content
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise AssertionError("MCP tool did not return a successful structured envelope")
    memories = payload.get(envelope_key)
    if isinstance(memories, dict):
        memories = memories.get("results")
    if not isinstance(memories, list):
        raise AssertionError("MCP tool returned an unexpected memory envelope")
    return {str(item["id"]) for item in memories if isinstance(item, dict) and "id" in item}


def main() -> None:
    history_workspace = tempfile.TemporaryDirectory(prefix="ram0-task7-real-stack-")
    history_directory = Path(history_workspace.name).resolve()
    history_db = history_directory / "history.db"
    telemetry_state = history_directory / "telemetry.json"
    repository_history = SERVER.joinpath("history", "history.db").resolve()
    workspace_mode = history_directory.stat().st_mode & 0o777
    if (
        history_directory.parent != Path(tempfile.gettempdir()).resolve()
        or not history_directory.name.startswith("ram0-task7-real-stack-")
        or history_directory.is_symlink()
        or history_directory.stat().st_uid != os.getuid()
        or workspace_mode != 0o700
        or history_db.parent != history_directory
        or history_db == repository_history
    ):
        history_workspace.cleanup()
        raise RuntimeError("Unsafe verifier history workspace")
    db_port = free_port()
    api_port = free_port()
    while api_port == db_port:
        api_port = free_port()
    base_url = f"http://127.0.0.1:{api_port}"
    server: uvicorn.Server | None = None
    server_thread: threading.Thread | None = None
    log_stream = io.StringIO()
    log_handler = logging.StreamHandler(log_stream)
    original_root_handlers: list[logging.Handler] = []
    original_access_handlers: list[logging.Handler] = []
    original_access_propagate = True
    memory_logger = logging.getLogger("mem0.memory.main")
    original_memory_log_level = memory_logger.level
    canaries: list[str] = []
    matrix: dict[str, dict[str, bool]] = {
        name: {"JWT": False, "API key": False}
        for name in (
            "create/list/search/get/update/delete/history",
            "entities",
            "category counts",
            "category jobs",
            "reclassification start",
            "reset",
            "forbidden user_id -> 422",
            "foreign/missing IDs -> generic 404",
            "no cross-owner mutation",
            "app scopes/entities",
        )
    }

    try:
        run(
            "docker",
            "run",
            "-d",
            "--name",
            CONTAINER,
            "-e",
            "POSTGRES_HOST_AUTH_METHOD=trust",
            "-p",
            f"127.0.0.1:{db_port}:5432",
            "pgvector/pgvector:pg17",
        )
        wait_for_postgres(db_port)
        for attempt in range(20):
            try:
                run("docker", "exec", CONTAINER, "createdb", "-U", "postgres", "mem0_app")
                break
            except subprocess.CalledProcessError:
                if attempt == 19:
                    raise
                time.sleep(0.1)

        environment = os.environ.copy()
        environment.update(
            {
                "POSTGRES_HOST": "127.0.0.1",
                "POSTGRES_PORT": str(db_port),
                "POSTGRES_USER": "postgres",
                "POSTGRES_PASSWORD": "",
                "POSTGRES_DB": "postgres",
                "APP_DB_NAME": "mem0_app",
                "POSTGRES_COLLECTION_NAME": "memories",
                "JWT_SECRET": secrets.token_urlsafe(48),
                "OPENAI_API_KEY": "external-call-disabled",
                "CATEGORY_WORKER_ENABLED": "false",
                "MEM0_TELEMETRY": "false",
                "MEM0_TELEMETRY_STATE_PATH": str(telemetry_state),
                "HISTORY_DB_PATH": str(history_db),
                "DASHBOARD_URL": "http://127.0.0.1:3000",
            }
        )
        os.environ.update(environment)
        run(sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "007", cwd=SERVER, env=environment)

        sys.path.insert(0, str(ROOT))
        sys.path.insert(0, str(SERVER))
        import main as api
        from category_models import CategoryJobState
        from models import CategoryJob, RequestLog, Settings, User
        from sqlalchemy import func, select

        memory = api.get_memory_instance()

        def deterministic_embed(text: str, *_args: Any, **_kwargs: Any) -> list[float]:
            digest = hashlib.sha256(text.encode()).digest()
            return [((digest[index % len(digest)] + 1) / 256.0) for index in range(1536)]

        memory.embedding_model.embed = deterministic_embed

        legacy_contents = [secrets.token_urlsafe(18), secrets.token_urlsafe(18)]
        canaries.extend(legacy_contents)
        legacy_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
        legacy_owner_values = ["legacy-account-alpha", "legacy-account-beta"]
        now = datetime.now(timezone.utc).isoformat()
        legacy_payloads = [
            {
                "data": content,
                "user_id": legacy_owner_values[index],
                "hash": hashlib.md5(content.encode()).hexdigest(),
                "created_at": now,
                "updated_at": now,
                "agent_id": f"legacy-agent-{index}",
                "run_id": f"legacy-run-{index}",
                "categories": [f"legacy_category_{index}"],
                "category_status": "unclassified",
                "custom_legacy_field": f"preserve-{index}",
            }
            for index, content in enumerate(legacy_contents)
        ]
        memory.vector_store.insert(
            vectors=[deterministic_embed(content) for content in legacy_contents],
            ids=legacy_ids,
            payloads=legacy_payloads,
        )
        legacy_job_id = uuid.uuid4()
        run(
            "docker",
            "exec",
            CONTAINER,
            "psql",
            "-U",
            "postgres",
            "-d",
            "mem0_app",
            "-c",
            "INSERT INTO category_jobs "
            "(id, memory_id, state, catalog_snapshot, memory_hash, attempts, next_attempt_at, created_at, updated_at) "
            f"VALUES ('{legacy_job_id}', '{legacy_ids[0]}', 'queued', "
            '\'[{"name":"legacy","description":"legacy"}]\'::jsonb, '
            f"'{legacy_payloads[0]['hash']}', 0, now(), now(), now());",
        )

        run("docker", "exec", CONTAINER, "pg_dump", "-U", "postgres", "-Fc", "-d", "postgres", "-f", PRE_VECTOR_DUMP)
        run("docker", "exec", CONTAINER, "pg_dump", "-U", "postgres", "-Fc", "-d", "mem0_app", "-f", PRE_APP_DUMP)
        run("docker", "exec", CONTAINER, "createdb", "-U", "postgres", "ram0_pre_vector_restore")
        run("docker", "exec", CONTAINER, "createdb", "-U", "postgres", "ram0_pre_app_restore")
        run(
            "docker",
            "exec",
            CONTAINER,
            "pg_restore",
            "-U",
            "postgres",
            "-d",
            "ram0_pre_vector_restore",
            PRE_VECTOR_DUMP,
        )
        run("docker", "exec", CONTAINER, "pg_restore", "-U", "postgres", "-d", "ram0_pre_app_restore", PRE_APP_DUMP)
        pre_legacy_ids = ", ".join(f"'{memory_id}'" for memory_id in legacy_ids)
        restored_pre_vector = run(
            "docker",
            "exec",
            CONTAINER,
            "psql",
            "-U",
            "postgres",
            "-d",
            "ram0_pre_vector_restore",
            "-tAc",
            f"SELECT count(*) = 2 AND count(DISTINCT payload->>'user_id') = 2 "
            f"AND bool_and(payload->>'user_id' IN ('{legacy_owner_values[0]}', '{legacy_owner_values[1]}')) "
            f"FROM memories WHERE id::text IN ({pre_legacy_ids});",
            capture=True,
        )
        restored_pre_app = run(
            "docker",
            "exec",
            CONTAINER,
            "psql",
            "-U",
            "postgres",
            "-d",
            "ram0_pre_app_restore",
            "-tAc",
            "SELECT (SELECT version_num FROM alembic_version) = '007' "
            f"AND (SELECT count(*) FROM category_jobs WHERE id = '{legacy_job_id}' "
            f"AND memory_id = '{legacy_ids[0]}' AND state = 'queued') = 1 "
            "AND NOT EXISTS (SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'category_jobs' AND column_name = 'owner_id');",
            capture=True,
        )
        if restored_pre_vector != "t" or restored_pre_app != "t":
            raise AssertionError("pre-upgrade PostgreSQL backup validation failed")
        run("docker", "exec", CONTAINER, "rm", "-f", PRE_VECTOR_DUMP, PRE_APP_DUMP)
        run("docker", "exec", CONTAINER, "dropdb", "-U", "postgres", "ram0_pre_vector_restore")
        run("docker", "exec", CONTAINER, "dropdb", "-U", "postgres", "ram0_pre_app_restore")

        run(sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head", cwd=SERVER, env=environment)

        root_logger = logging.getLogger()
        access_logger = logging.getLogger("uvicorn.access")
        original_root_handlers = list(root_logger.handlers)
        original_access_handlers = list(access_logger.handlers)
        original_access_propagate = access_logger.propagate
        for handler in original_root_handlers:
            root_logger.removeHandler(handler)
        for handler in original_access_handlers:
            access_logger.removeHandler(handler)
        root_logger.addHandler(log_handler)
        access_logger.addHandler(log_handler)
        access_logger.propagate = False
        memory_logger.setLevel(logging.DEBUG)

        def start_api() -> tuple[uvicorn.Server, threading.Thread]:
            instance = uvicorn.Server(
                uvicorn.Config(api.app, host="127.0.0.1", port=api_port, log_config=None, access_log=True)
            )
            thread = threading.Thread(target=instance.run, name="task7-uvicorn", daemon=True)
            thread.start()
            for _ in range(100):
                try:
                    response = httpx.get(f"{base_url}/auth/setup-status", timeout=1.0)
                    if response.status_code == 200:
                        return instance, thread
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
            raise RuntimeError("FastAPI did not become ready")

        def stop_api() -> None:
            nonlocal server, server_thread
            if server is not None:
                server.should_exit = True
            if server_thread is not None:
                server_thread.join(timeout=10)
                if server_thread.is_alive():
                    raise RuntimeError("FastAPI did not stop")
            server = None
            server_thread = None

        server, server_thread = start_api()
        waiting_rows = [memory.vector_store.get(vector_id=item).payload for item in legacy_ids]
        if waiting_rows != legacy_payloads:
            raise AssertionError("zero-user startup mutated legacy memory")
        with api.SessionLocal() as session:
            if session.get(Settings, "memory_ownership_version") is not None:
                raise AssertionError("zero-user startup persisted ownership version")
            if session.get(CategoryJob, legacy_job_id).owner_id is not None:
                raise AssertionError("zero-user startup claimed legacy job")

        admin_password = secrets.token_urlsafe(18)
        member_password = secrets.token_urlsafe(18)
        canaries.extend([admin_password, member_password])
        with httpx.Client(base_url=base_url, timeout=20.0) as client:
            registered = client.post(
                "/auth/register",
                json={"name": "Task Admin", "email": "task7.admin@example.com", "password": admin_password},
            )
            if registered.status_code != 200:
                from memory_owner_migration import _provider_name

                with api.SessionLocal() as session:
                    users = list(session.scalars(select(User)).all())
                raise AssertionError(
                    "bootstrap migration diagnostic: "
                    f"status={registered.status_code} user_count={len(users)} "
                    f"roles={[user.role for user in users]} provider={_provider_name(memory)!r}"
                )
            assert_status(registered, 200, "bootstrap registration")
            admin_jwt = registered.json()["access_token"]
            admin_headers = {"Authorization": f"Bearer {admin_jwt}"}
            admin_me = client.get("/auth/me", headers=admin_headers)
            assert_status(admin_me, 200, "admin identity")
            admin_id = admin_me.json()["id"]

            migrated_rows = [memory.vector_store.get(vector_id=item).payload for item in legacy_ids]
            for before, after in zip(legacy_payloads, migrated_rows):
                before_without_owner = {key: value for key, value in before.items() if key != "user_id"}
                after_without_owner = {key: value for key, value in after.items() if key != "user_id"}
                if after.get("user_id") != admin_id or after_without_owner != before_without_owner:
                    raise AssertionError("legacy migration did not preserve payload")
            with api.SessionLocal() as session:
                marker = session.get(Settings, "memory_ownership_version")
                job = session.get(CategoryJob, legacy_job_id)
                if marker is None or marker.value != "1" or str(job.owner_id) != admin_id:
                    raise AssertionError("legacy ownership migration did not complete")

            invitation = client.post(
                "/admin/invitations", headers=admin_headers, json={"email": "task7.member@example.com"}
            )
            assert_status(invitation, 201, "member invitation")
            invitation_token = invitation.json()["invite_url"].split("#token=", 1)[1]
            canaries.append(invitation_token)
            accepted = client.post(
                "/auth/invitations/accept", json={"token": invitation_token, "password": member_password}
            )
            assert_status(accepted, 200, "member acceptance")
            member_jwt = accepted.json()["access_token"]
            member_headers = {"Authorization": f"Bearer {member_jwt}"}
            member_me = client.get("/auth/me", headers=member_headers)
            assert_status(member_me, 200, "member identity")
            member_id = member_me.json()["id"]

            admin_key_response = client.post("/api-keys", headers=admin_headers, json={"label": "task7-admin"})
            member_key_response = client.post("/api-keys", headers=member_headers, json={"label": "task7-member"})
            assert_status(admin_key_response, 201, "admin API key")
            assert_status(member_key_response, 201, "member API key")
            admin_key = admin_key_response.json()["key"]
            member_key = member_key_response.json()["key"]
            canaries.extend([admin_key, member_key])
            admin_key_headers = {"X-API-Key": admin_key}
            member_key_headers = {"X-API-Key": member_key}

            async def verify_mcp_app_scopes() -> None:
                from fastmcp import Client as McpClient

                app_id = f"verify-{uuid.uuid4().hex}-mcp"
                other_app_id = f"verify-{uuid.uuid4().hex}-mcp-other"
                owner_memories: dict[str, set[str]] = {}
                for role, key in (("admin", admin_key), ("member", member_key)):
                    async with McpClient(f"{base_url}/mcp", auth=key) as mcp:
                        created_ids = set()
                        for scope, selected_app in (("global", None), ("project", app_id), ("project", other_app_id)):
                            content = secrets.token_urlsafe(18)
                            canaries.append(content)
                            arguments = {"content": content, "scope": scope}
                            if selected_app is not None:
                                arguments["app_id"] = selected_app
                            result = await mcp.call_tool("remember", arguments)
                            created_ids.update(mcp_memory_ids(result, "result"))
                        owner_memories[role] = created_ids
                        reads = {
                            "default": await mcp.call_tool("list_memories", {"limit": 100, "app_id": app_id}),
                            "project": await mcp.call_tool(
                                "list_memories", {"limit": 100, "scope": "project", "app_id": app_id}
                            ),
                            "global": await mcp.call_tool("list_memories", {"limit": 100, "scope": "global"}),
                        }
                        read_ids = {
                            name: mcp_memory_ids(result, "memories")
                            for name, result in reads.items()
                        }
                        if len(read_ids["default"] & created_ids) != 2:
                            raise AssertionError("MCP default scope did not include project plus global")
                        if len(read_ids["project"] & created_ids) != 1:
                            raise AssertionError("MCP project scope was not exact")
                        if not created_ids.issubset(read_ids["global"]):
                            raise AssertionError("MCP global scope was not owner-wide")
                if owner_memories["admin"] & owner_memories["member"]:
                    raise AssertionError("MCP app scope crossed owner boundary")

            asyncio.run(verify_mcp_app_scopes())

            credentials = {
                "JWT": [("admin", admin_id, admin_headers), ("member", member_id, member_headers)],
                "API key": [
                    ("admin", admin_id, admin_key_headers),
                    ("member", member_id, member_key_headers),
                ],
            }

            app_ids = {
                "app-a": f"verify-{uuid.uuid4().hex}-a",
                "app-b": f"verify-{uuid.uuid4().hex}-b",
            }
            scope_memory_ids: dict[str, dict[str, str]] = {"admin": {}, "member": {}}
            for role, headers in (("admin", admin_headers), ("member", member_headers)):
                for scope_name, app_id in (
                    ("global", None),
                    ("app-a", app_ids["app-a"]),
                    ("app-b", app_ids["app-b"]),
                ):
                    content = secrets.token_urlsafe(18)
                    canaries.append(content)
                    body: dict[str, Any] = {
                        "messages": [{"role": "user", "content": content}],
                        "infer": False,
                    }
                    if app_id is not None:
                        body["app_id"] = app_id
                    created = client.post("/memories", headers=headers, json=body)
                    assert_status(created, 200, f"{role} {scope_name} memory create")
                    scope_memory_ids[role][scope_name] = created.json()["results"][0]["id"]
                memory.vector_store._patch_payload(
                    scope_memory_ids[role]["app-a"],
                    {"categories": [f"app_scope_{role}"], "category_status": "classified"},
                )

            # Both accounts deliberately use the same app names. Ownership must
            # remain the outer boundary for every default, project, and global read.
            read_contracts = {
                "default project plus global": {
                    "filters": {"OR": [{"app_id": app_ids["app-a"]}, {"app_id": None}]},
                    "included": ("global", "app-a"),
                    "excluded": ("app-b",),
                },
                "project exact": {
                    "filters": {"app_id": app_ids["app-a"]},
                    "included": ("app-a",),
                    "excluded": ("global", "app-b"),
                },
                "global owner-wide": {
                    "filters": None,
                    "included": ("global", "app-a", "app-b"),
                    "excluded": (),
                },
            }
            for auth_kind, owners in credentials.items():
                delete_app = f"verify-{uuid.uuid4().hex}-delete"
                delete_app_memory_ids: dict[str, str] = {}
                for role, _owner_id, headers in owners:
                    delete_content = secrets.token_urlsafe(18)
                    canaries.append(delete_content)
                    disposable = client.post(
                        "/memories",
                        headers=headers,
                        json={
                            "messages": [{"role": "user", "content": delete_content}],
                            "infer": False,
                            "app_id": delete_app,
                        },
                    )
                    assert_status(disposable, 200, f"{auth_kind} {role} app deletion setup")
                    delete_app_memory_ids[role] = disposable.json()["results"][0]["id"]

                for role, _owner_id, headers in owners:
                    other_role = "member" if role == "admin" else "admin"
                    own_ids = scope_memory_ids[role]
                    foreign_ids = set(scope_memory_ids[other_role].values())
                    for label, contract in read_contracts.items():
                        params = {"top_k": "1000"}
                        search_body: dict[str, Any] = {"query": secrets.token_urlsafe(18), "top_k": 1000}
                        canaries.append(search_body["query"])
                        if contract["filters"] is not None:
                            params["filters"] = json.dumps(contract["filters"])
                            search_body["filters"] = contract["filters"]
                        listed = client.get("/memories", headers=headers, params=params)
                        searched = client.post("/search", headers=headers, json=search_body)
                        assert_status(listed, 200, f"{auth_kind} {role} {label} list")
                        assert_status(searched, 200, f"{auth_kind} {role} {label} search")
                        for result_ids in (ids(listed.json()), ids(searched.json())):
                            if not {own_ids[name] for name in contract["included"]}.issubset(result_ids):
                                raise AssertionError(f"{label} omitted an expected memory")
                            if {own_ids[name] for name in contract["excluded"]} & result_ids:
                                raise AssertionError(f"{label} crossed app scope")
                            if foreign_ids & result_ids:
                                raise AssertionError(f"{label} crossed owner scope")

                    app_entities = {
                        item["id"]: item
                        for item in client.get("/entities", headers=headers).json()
                        if item["type"] == "app"
                    }
                    if app_entities.get(app_ids["app-a"], {}).get("total_memories") != 1:
                        raise AssertionError("same-name app entity count crossed owner boundary")
                    if app_entities.get(delete_app, {}).get("total_memories") != 1:
                        raise AssertionError("same-label deletion fixture crossed owner boundary")

                delete_owner_index = 0 if auth_kind == "JWT" else 1
                role, _owner_id, headers = owners[delete_owner_index]
                other_role = "member" if role == "admin" else "admin"
                deleted_app = client.delete(f"/entities/app/{delete_app}", headers=headers)
                assert_status(deleted_app, 200, f"{auth_kind} {role} app entity deletion")
                if memory.vector_store.get(vector_id=delete_app_memory_ids[role]) is not None:
                    raise AssertionError("app entity deletion retained caller same-label memory")
                if memory.vector_store.get(vector_id=delete_app_memory_ids[other_role]) is None:
                    raise AssertionError("app entity deletion removed foreign same-label memory")
                if memory.vector_store.get(vector_id=scope_memory_ids[role]["app-a"]) is None:
                    raise AssertionError("app entity deletion removed a different app")
                if memory.vector_store.get(vector_id=scope_memory_ids[other_role]["app-a"]) is None:
                    raise AssertionError("app entity deletion crossed owner boundary")
                matrix["app scopes/entities"][auth_kind] = True

            sentinel: dict[str, str] = {}
            sentinel_agent = {"admin": "agent-admin-only", "member": "agent-member-only"}
            sentinel_category = {"admin": "category_admin_only", "member": "category_member_only"}
            for role, headers in (("admin", admin_headers), ("member", member_headers)):
                content = secrets.token_urlsafe(18)
                canaries.append(content)
                created = client.post(
                    "/memories",
                    headers=headers,
                    json={
                        "messages": [{"role": "user", "content": content}],
                        "infer": False,
                        "agent_id": sentinel_agent[role],
                        "run_id": f"run-{role}-only",
                    },
                )
                assert_status(created, 200, f"{role} sentinel create")
                sentinel[role] = created.json()["results"][0]["id"]
                memory.vector_store._patch_payload(
                    sentinel[role],
                    {"categories": [sentinel_category[role]], "category_status": "classified"},
                )

            for auth_kind, owners in credentials.items():
                for role, owner_id, headers in owners:
                    other_role = "member" if role == "admin" else "admin"
                    content = secrets.token_urlsafe(18)
                    updated_content = secrets.token_urlsafe(18)
                    search_query = secrets.token_urlsafe(18)
                    canaries.extend([content, updated_content, search_query])
                    created = client.post(
                        "/memories",
                        headers=headers,
                        json={
                            "messages": [{"role": "user", "content": content}],
                            "infer": False,
                            "agent_id": f"crud-{role}",
                            "run_id": f"crud-{auth_kind.replace(' ', '-')}",
                        },
                    )
                    assert_status(created, 200, f"{auth_kind} {role} create")
                    memory_id = created.json()["results"][0]["id"]
                    listed = client.get("/memories", headers=headers)
                    assert_status(listed, 200, f"{auth_kind} {role} list")
                    listed_ids = ids(listed.json())
                    if memory_id not in listed_ids or sentinel[other_role] in listed_ids:
                        raise AssertionError("owner-scoped list failed")
                    searched = client.post("/search", headers=headers, json={"query": search_query, "top_k": 100})
                    assert_status(searched, 200, f"{auth_kind} {role} search")
                    searched_ids = ids(searched.json())
                    if memory_id not in searched_ids or sentinel[other_role] in searched_ids:
                        raise AssertionError("owner-scoped search failed")
                    fetched = client.get(f"/memories/{memory_id}", headers=headers)
                    assert_status(fetched, 200, f"{auth_kind} {role} get")
                    if fetched.json().get("user_id") != owner_id:
                        raise AssertionError("credential owner attribution failed")
                    updated = client.put(f"/memories/{memory_id}", headers=headers, json={"text": updated_content})
                    assert_status(updated, 200, f"{auth_kind} {role} update")
                    history = client.get(f"/memories/{memory_id}/history", headers=headers)
                    assert_status(history, 200, f"{auth_kind} {role} history")
                    if not history.json():
                        raise AssertionError("memory history was empty")
                    deleted = client.delete(f"/memories/{memory_id}", headers=headers)
                    assert_status(deleted, 200, f"{auth_kind} {role} delete")
                    if memory.vector_store.get(vector_id=sentinel[other_role]) is None:
                        raise AssertionError("CRUD mutated foreign owner")
                matrix["create/list/search/get/update/delete/history"][auth_kind] = True
                matrix["no cross-owner mutation"][auth_kind] = True

            for auth_kind, headers in (("JWT", admin_headers), ("API key", admin_key_headers)):
                forbidden = [
                    client.post(
                        "/memories",
                        headers=headers,
                        json={"messages": [{"role": "user", "content": "rejected"}], "user_id": member_id},
                    ),
                    client.get(f"/memories?user_id={member_id}", headers=headers),
                    client.post("/search", headers=headers, json={"query": "rejected", "user_id": member_id}),
                ]
                if any(response.status_code != 422 for response in forbidden):
                    raise AssertionError("caller-supplied user_id was not rejected")
                matrix["forbidden user_id -> 422"][auth_kind] = True

            random_id = str(uuid.uuid4())
            for auth_kind, owners in credentials.items():
                for role, _owner_id, headers in owners:
                    other = sentinel["member" if role == "admin" else "admin"]
                    for method, suffix, body in (
                        ("GET", "", None),
                        ("GET", "/history", None),
                        ("PUT", "", {"text": "rejected"}),
                        ("DELETE", "", None),
                    ):
                        foreign = client.request(method, f"/memories/{other}{suffix}", headers=headers, json=body)
                        missing = client.request(method, f"/memories/{random_id}{suffix}", headers=headers, json=body)
                        if foreign.status_code != 404 or missing.status_code != 404 or foreign.json() != missing.json():
                            raise AssertionError("foreign and missing memory responses differ")
                    if memory.vector_store.get(vector_id=other) is None:
                        raise AssertionError("foreign direct-ID request mutated memory")
                matrix["foreign/missing IDs -> generic 404"][auth_kind] = True

            for auth_kind, owners in credentials.items():
                for role, _owner_id, headers in owners:
                    entities = client.get("/entities", headers=headers)
                    assert_status(entities, 200, f"{auth_kind} {role} entities")
                    entity_ids = {item["id"] for item in entities.json()}
                    other_role = "member" if role == "admin" else "admin"
                    if sentinel_agent[role] not in entity_ids or sentinel_agent[other_role] in entity_ids:
                        raise AssertionError("entities crossed owner boundary")
                    categories = client.get("/categories", headers=headers)
                    assert_status(categories, 200, f"{auth_kind} {role} category counts")
                    counts = categories.json()["counts"]
                    if counts.get(sentinel_category[role]) != 1 or sentinel_category[other_role] in counts:
                        raise AssertionError("category counts crossed owner boundary")
                matrix["entities"][auth_kind] = True
                matrix["category counts"][auth_kind] = True

            reclass_content = secrets.token_urlsafe(18)
            member_reclass_content = secrets.token_urlsafe(18)
            canaries.extend([reclass_content, member_reclass_content])
            reclass_id = str(uuid.uuid4())
            member_reclass_id = str(uuid.uuid4())
            for memory_id, owner_id, content in (
                (reclass_id, admin_id, reclass_content),
                (member_reclass_id, member_id, member_reclass_content),
            ):
                payload = {
                    "data": content,
                    "hash": hashlib.md5(content.encode()).hexdigest(),
                    "created_at": now,
                    "updated_at": now,
                    "user_id": owner_id,
                    "category_status": "unclassified",
                    "app_id": app_ids["app-a"],
                }
                memory.vector_store.insert(vectors=[deterministic_embed(content)], ids=[memory_id], payloads=[payload])

            preview = client.post("/categories/reclassify/preview", headers=admin_headers, json={"scope": "all"})
            assert_status(preview, 200, "JWT reclassification preview")
            started = client.post(
                "/categories/reclassify",
                headers=admin_headers,
                json={"scope": "all", "confirm": "RECLASSIFY"},
            )
            assert_status(started, 202, "JWT reclassification start")
            if started.json()["created_jobs"] < 1:
                raise AssertionError("JWT reclassification did not create a job")
            started_again = client.post(
                "/categories/reclassify",
                headers=admin_key_headers,
                json={"scope": "all", "confirm": "RECLASSIFY"},
            )
            assert_status(started_again, 202, "API-key reclassification start")
            if started_again.json()["eligible_memories"] < 1:
                raise AssertionError("API-key reclassification did not evaluate owner memories")
            for auth_kind, headers in (("JWT", member_headers), ("API key", member_key_headers)):
                member_preview = client.post("/categories/reclassify/preview", headers=headers, json={"scope": "all"})
                member_start = client.post(
                    "/categories/reclassify",
                    headers=headers,
                    json={"scope": "all", "confirm": "RECLASSIFY"},
                )
                if member_preview.status_code != 403 or member_start.status_code != 403:
                    raise AssertionError("member reclassification was not forbidden")
                matrix["reclassification start"][auth_kind] = True

            for auth_kind, admin_auth, member_auth in (
                ("JWT", admin_headers, member_headers),
                ("API key", admin_key_headers, member_key_headers),
            ):
                jobs = client.get("/categories/jobs", headers=admin_auth)
                assert_status(jobs, 200, f"{auth_kind} category jobs")
                job_memory_ids = {item["memory_id"] for item in jobs.json()}
                if reclass_id not in job_memory_ids or member_reclass_id in job_memory_ids:
                    raise AssertionError("category jobs crossed owner boundary")
                forbidden_jobs = client.get("/categories/jobs", headers=member_auth)
                assert_status(forbidden_jobs, 403, f"{auth_kind} member category jobs")
                matrix["category jobs"][auth_kind] = True
                matrix["reclassification start"][auth_kind] = True

            with api.SessionLocal() as session:
                reclass_job = session.scalar(select(CategoryJob).where(CategoryJob.memory_id == reclass_id))
                foreign_job = session.scalar(select(CategoryJob).where(CategoryJob.memory_id == member_reclass_id))
                if reclass_job is None or str(reclass_job.owner_id) != admin_id or foreign_job is not None:
                    raise AssertionError("started reclassification job was not owner-scoped")
            for memory_id in (reclass_id, member_reclass_id):
                if memory.vector_store.get(vector_id=memory_id).payload.get("app_id") != app_ids["app-a"]:
                    raise AssertionError("reclassification changed app_id")

            run(
                "docker",
                "exec",
                CONTAINER,
                "pg_dump",
                "-U",
                "postgres",
                "-Fc",
                "-d",
                "postgres",
                "-f",
                POST_VECTOR_DUMP,
            )
            run(
                "docker",
                "exec",
                CONTAINER,
                "pg_dump",
                "-U",
                "postgres",
                "-Fc",
                "-d",
                "mem0_app",
                "-f",
                POST_APP_DUMP,
            )
            run("docker", "exec", CONTAINER, "createdb", "-U", "postgres", "ram0_post_vector_restore")
            run("docker", "exec", CONTAINER, "createdb", "-U", "postgres", "ram0_post_app_restore")
            run(
                "docker",
                "exec",
                CONTAINER,
                "pg_restore",
                "-U",
                "postgres",
                "-d",
                "ram0_post_vector_restore",
                POST_VECTOR_DUMP,
            )
            run(
                "docker",
                "exec",
                CONTAINER,
                "pg_restore",
                "-U",
                "postgres",
                "-d",
                "ram0_post_app_restore",
                POST_APP_DUMP,
            )
            restored_legacy_ids = ", ".join(f"'{memory_id}'" for memory_id in legacy_ids)
            restored_scope_ids = ", ".join(
                f"'{memory_id}'" for role_ids in scope_memory_ids.values() for memory_id in role_ids.values()
            )
            restored_global_ids = ", ".join(
                f"'{role_ids['global']}'" for role_ids in scope_memory_ids.values()
            )
            restored_app_a_ids = ", ".join(
                f"'{role_ids['app-a']}'" for role_ids in scope_memory_ids.values()
            )
            restored_app_b_ids = ", ".join(
                f"'{role_ids['app-b']}'" for role_ids in scope_memory_ids.values()
            )
            restored_vector = run(
                "docker",
                "exec",
                CONTAINER,
                "psql",
                "-U",
                "postgres",
                "-d",
                "ram0_post_vector_restore",
                "-tAc",
                "SELECT (count(*) > 0) AND bool_and(payload ? 'user_id') "
                "AND count(DISTINCT payload->>'user_id') = 2 "
                f"AND count(*) FILTER (WHERE id::text IN ({restored_legacy_ids}) "
                f"AND payload->>'user_id' = '{admin_id}') = 2 FROM memories;",
                capture=True,
            )
            restored_app_scopes = run(
                "docker",
                "exec",
                CONTAINER,
                "psql",
                "-U",
                "postgres",
                "-d",
                "ram0_post_vector_restore",
                "-tAc",
                f"SELECT count(*) FILTER (WHERE id::text IN ({restored_scope_ids})) = 6 "
                f"AND count(*) FILTER (WHERE id::text IN ({restored_global_ids}) AND NOT payload ? 'app_id') = 2 "
                f"AND count(*) FILTER (WHERE id::text IN ({restored_app_a_ids}) "
                f"AND payload->>'app_id' = '{app_ids['app-a']}') = 2 "
                f"AND count(*) FILTER (WHERE id::text IN ({restored_app_b_ids}) "
                f"AND payload->>'app_id' = '{app_ids['app-b']}') = 2 FROM memories;",
                capture=True,
            )
            restored_app = run(
                "docker",
                "exec",
                CONTAINER,
                "psql",
                "-U",
                "postgres",
                "-d",
                "ram0_post_app_restore",
                "-tAc",
                "SELECT (SELECT count(*) FROM users) = 2 "
                "AND (SELECT value FROM settings WHERE key='memory_ownership_version') = '1' "
                "AND (SELECT count(*) FROM category_jobs WHERE owner_id IS NULL) = 0 "
                f"AND (SELECT count(*) FROM category_jobs WHERE id = '{legacy_job_id}' "
                f"AND owner_id = '{admin_id}') = 1;",
                capture=True,
            )
            if restored_vector != "t" or restored_app_scopes != "t" or restored_app != "t":
                raise AssertionError("restored PostgreSQL backup validation failed")
            run("docker", "exec", CONTAINER, "rm", "-f", POST_VECTOR_DUMP, POST_APP_DUMP)
            dump_check = run(
                "docker",
                "exec",
                CONTAINER,
                "sh",
                "-c",
                f"test ! -e {PRE_VECTOR_DUMP} && test ! -e {PRE_APP_DUMP} "
                f"&& test ! -e {POST_VECTOR_DUMP} && test ! -e {POST_APP_DUMP} && echo clean",
                capture=True,
            )
            if dump_check != "clean":
                raise AssertionError("backup artifacts were not removed")
            run("docker", "exec", CONTAINER, "dropdb", "-U", "postgres", "ram0_post_vector_restore")
            run("docker", "exec", CONTAINER, "dropdb", "-U", "postgres", "ram0_post_app_restore")

            stop_api()
            server, server_thread = start_api()
            repeated = client.get("/memories", headers=member_key_headers)
            assert_status(repeated, 200, "repeat-start API-key ownership")
            if sentinel["member"] not in ids(repeated.json()) or sentinel["admin"] in ids(repeated.json()):
                raise AssertionError("repeat startup changed ownership")

            reset_credentials = [
                ("JWT", "admin", admin_headers),
                ("JWT", "member", member_headers),
                ("API key", "admin", admin_key_headers),
                ("API key", "member", member_key_headers),
            ]
            for auth_kind, role, headers in reset_credentials:
                other_role = "member" if role == "admin" else "admin"
                if memory.vector_store.get(vector_id=sentinel[role]) is None:
                    content = secrets.token_urlsafe(18)
                    canaries.append(content)
                    created = client.post(
                        "/memories",
                        headers=headers,
                        json={"messages": [{"role": "user", "content": content}], "infer": False},
                    )
                    assert_status(created, 200, f"{auth_kind} {role} reset setup")
                    sentinel[role] = created.json()["results"][0]["id"]
                if memory.vector_store.get(vector_id=sentinel[other_role]) is None:
                    other_headers = member_headers if other_role == "member" else admin_headers
                    content = secrets.token_urlsafe(18)
                    canaries.append(content)
                    created = client.post(
                        "/memories",
                        headers=other_headers,
                        json={"messages": [{"role": "user", "content": content}], "infer": False},
                    )
                    assert_status(created, 200, f"{auth_kind} foreign reset setup")
                    sentinel[other_role] = created.json()["results"][0]["id"]

                owner_id = admin_id if role == "admin" else member_id
                foreign_owner_id = member_id if role == "admin" else admin_id
                scoped_reset_ids: dict[str, str] = {}
                reset_owners = (
                    (role, headers),
                    (other_role, member_headers if other_role == "member" else admin_headers),
                )
                for reset_role, reset_headers in reset_owners:
                    reset_content = secrets.token_urlsafe(18)
                    canaries.append(reset_content)
                    created = client.post(
                        "/memories",
                        headers=reset_headers,
                        json={
                            "messages": [{"role": "user", "content": reset_content}],
                            "infer": False,
                            "app_id": app_ids["app-a"],
                        },
                    )
                    assert_status(created, 200, f"{auth_kind} {reset_role} scoped reset setup")
                    scoped_reset_ids[reset_role] = created.json()["results"][0]["id"]
                caller_job_ids: set[str] = set()
                foreign_job_ids: set[str] = set()
                with api.SessionLocal() as session:
                    for state in CategoryJobState:
                        caller_job = CategoryJob(
                            memory_id=f"reset-{auth_kind}-{role}-caller-{state.value}-{uuid.uuid4()}",
                            owner_id=uuid.UUID(owner_id),
                            state=state.value,
                            catalog_snapshot=[{"name": "reset", "description": "caller sentinel"}],
                            memory_hash=secrets.token_hex(16),
                        )
                        foreign_job = CategoryJob(
                            memory_id=f"reset-{auth_kind}-{role}-foreign-{state.value}-{uuid.uuid4()}",
                            owner_id=uuid.UUID(foreign_owner_id),
                            state=state.value,
                            catalog_snapshot=[{"name": "reset", "description": "foreign sentinel"}],
                            memory_hash=secrets.token_hex(16),
                        )
                        session.add_all((caller_job, foreign_job))
                        session.flush()
                        caller_job_ids.add(str(caller_job.id))
                        foreign_job_ids.add(str(foreign_job.id))
                    session.commit()

                reset = client.post("/reset", headers=headers)
                assert_status(reset, 200, f"{auth_kind} {role} reset")
                if memory.vector_store.get(vector_id=sentinel[role]) is not None:
                    raise AssertionError("reset retained owner memory")
                if memory.vector_store.get(vector_id=sentinel[other_role]) is None:
                    raise AssertionError("reset deleted foreign memory")
                if memory.vector_store.get(vector_id=scoped_reset_ids[role]) is not None:
                    raise AssertionError("reset retained owner app-scoped memory")
                if memory.vector_store.get(vector_id=scoped_reset_ids[other_role]) is None:
                    raise AssertionError("reset deleted foreign app-scoped memory")
                with api.SessionLocal() as session:
                    caller_jobs_after = session.scalar(
                        select(func.count(CategoryJob.id)).where(CategoryJob.owner_id == uuid.UUID(owner_id))
                    )
                    caller_sentinels_after = {
                        str(job_id)
                        for job_id in session.scalars(
                            select(CategoryJob.id).where(
                                CategoryJob.id.in_([uuid.UUID(item) for item in caller_job_ids])
                            )
                        ).all()
                    }
                    foreign_jobs_after = {
                        str(job_id)
                        for job_id in session.scalars(
                            select(CategoryJob.id).where(
                                CategoryJob.id.in_([uuid.UUID(item) for item in foreign_job_ids])
                            )
                        ).all()
                    }
                if caller_jobs_after != 0 or caller_sentinels_after:
                    raise AssertionError("reset retained caller category jobs")
                if foreign_jobs_after != foreign_job_ids:
                    raise AssertionError("reset deleted foreign category jobs")
                pollable_foreign_jobs = {
                    str(job.id) for job in api.get_category_service().list_jobs(owner_id=foreign_owner_id, limit=100)
                }
                if not foreign_job_ids.issubset(pollable_foreign_jobs):
                    raise AssertionError("foreign category jobs were not owner-scoped and pollable after reset")
                matrix["reset"][auth_kind] = True
                matrix["no cross-owner mutation"][auth_kind] = True

        stop_api()
        time.sleep(0.2)
        log_handler.flush()
        captured_logs = log_stream.getvalue()
        if any(canary and canary in captured_logs for canary in canaries):
            raise AssertionError("application/access logs contained a sensitive canary")
        if "Creating memory id=" not in captured_logs:
            raise AssertionError("DEBUG memory-creation log redaction path was not exercised")
        required_access_paths = ("POST /memories", "POST /search", "POST /reset", "POST /categories/reclassify")
        if any(path not in captured_logs for path in required_access_paths):
            raise AssertionError("live access logs did not cover required routes")
        with api.SessionLocal() as session:
            request_log_count = session.scalar(select(func.count(RequestLog.id)))
            request_paths = [path for path in session.scalars(select(RequestLog.path)).all()]
        if not request_log_count or any(
            canary and any(canary in path for path in request_paths) for canary in canaries
        ):
            raise AssertionError("persisted request-log redaction validation failed")

        if not history_db.is_file() or history_db.resolve().parent != history_directory:
            raise AssertionError("history database was not confined to the verifier-owned workspace")
        print("REAL_STACK_PASS migration=ready idempotent=true backup_restore=true logs_redacted=true app_scopes=true")
        print("surface | JWT | API key")
        for surface, modes in matrix.items():
            if not all(modes.values()):
                raise AssertionError(f"incomplete matrix row: {surface}")
            print(f"{surface} | PASS | PASS")
        print(
            "LOG_REDACTION_PASS invitation_token_absent=true password_absent=true "
            "api_key_absent=true search_query_absent=true memory_content_absent=true"
        )
        print(
            "PRE_UPGRADE_BACKUP_RESTORE_PASS distinct_legacy_owners=true legacy_job_state=true artifacts_removed=true"
        )
        print("POST_MIGRATION_BACKUP_RESTORE_PASS vector_owner_count=true app_owner_state=true artifacts_removed=true")
        print("HISTORY_ISOLATION_PASS verifier_owned=true repository_history_untouched=true cleanup_registered=true")
    finally:
        if server is not None:
            server.should_exit = True
        if server_thread is not None:
            server_thread.join(timeout=10)
        logging.getLogger().removeHandler(log_handler)
        memory_logger.setLevel(original_memory_log_level)
        access_logger = logging.getLogger("uvicorn.access")
        access_logger.removeHandler(log_handler)
        access_logger.propagate = original_access_propagate
        for handler in original_root_handlers:
            logging.getLogger().addHandler(handler)
        for handler in original_access_handlers:
            access_logger.addHandler(handler)
        try:
            run("docker", "rm", "-f", CONTAINER)
        except subprocess.CalledProcessError:
            pass
        history_workspace.cleanup()


if __name__ == "__main__":
    main()
