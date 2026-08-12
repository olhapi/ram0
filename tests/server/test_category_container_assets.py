"""Static isolation contracts for the real category container acceptance stack."""

# Modified for Ram0; see NOTICE and repository history.

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "server"
COMPOSE_PATH = SERVER / "docker-compose.categories-test.yaml"
VERIFY_PATH = SERVER / "scripts" / "verify_categories_container.sh"
UNRAID_COMPOSE_PATH = SERVER / "docker-compose.unraid.yaml"


def _service_block(compose: str, service: str) -> str:
    """Return one top-level Compose service block without requiring a YAML package."""
    lines = compose.splitlines()
    header = f"  {service}:"
    start = lines.index(header)
    end = next(
        (
            index
            for index in range(start + 1, len(lines))
            if re.fullmatch(r"  [a-zA-Z0-9_-]+:", lines[index])
            or (lines[index] and not lines[index].startswith(" "))
        ),
        len(lines),
    )
    return "\n".join(lines[start:end])


def test_acceptance_compose_publishes_only_loopback_api_and_dashboard_ports():
    compose = COMPOSE_PATH.read_text(encoding="utf-8")

    assert "\n    ports:" not in _service_block(compose, "postgres")
    assert "\n    ports:" not in _service_block(compose, "openai-stub")
    assert 'ports:\n      - "127.0.0.1:${RAM0_API_PORT:-18888}:8000"' in _service_block(
        compose, "ram0-api"
    )
    assert 'ports:\n      - "127.0.0.1:${RAM0_DASHBOARD_PORT:-13000}:3000"' in _service_block(
        compose, "ram0-dashboard"
    )


def test_acceptance_preflight_checks_only_the_two_published_loopback_ports():
    script = VERIFY_PATH.read_text(encoding="utf-8")

    assert "RAM0_POSTGRES_PORT" not in script
    assert "RAM0_OPENAI_STUB_PORT" not in script
    assert "two unused loopback host ports" in script
    assert 'sock.bind(("127.0.0.1", port))' in script


def test_stub_uses_narrow_server_context_and_dockerfile_specific_allowlist():
    compose = COMPOSE_PATH.read_text(encoding="utf-8")
    stub = _service_block(compose, "openai-stub")

    assert "build:\n      context: .\n      dockerfile: test-support.Dockerfile" in stub
    dockerfile = (SERVER / "test-support.Dockerfile").read_text(encoding="utf-8")
    assert "COPY test_support/openai_stub.py ./openai_stub.py" in dockerfile
    assert "COPY server/" not in dockerfile

    ignore_lines = {
        line.strip()
        for line in (SERVER / "test-support.Dockerfile.dockerignore")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert "**" in ignore_lines
    assert "!test_support" in ignore_lines
    assert "!test_support/openai_stub.py" in ignore_lines
    assert not any(
        line.startswith("!") and line not in {"!test_support", "!test_support/openai_stub.py"}
        for line in ignore_lines
    )


def test_real_acceptance_exercises_catalog_post_patch_delete_and_restores_user_order():
    script = VERIFY_PATH.read_text(encoding="utf-8")

    assert "api_request POST /categories" in script
    assert 'api_request PATCH "/categories/support"' in script
    assert 'api_request DELETE "/categories/customer_support"' in script
    assert "catalog POST appends the new definition in order" in script
    assert "catalog PATCH preserves order and changes name and description" in script
    assert "catalog DELETE restores the expected user catalog" in script


def test_unraid_compose_uses_digest_images_and_only_two_lan_ports():
    if shutil.which("docker") is None:
        pytest.skip("Docker Compose is unavailable")

    with tempfile.TemporaryDirectory() as tmp:
        temp_server = Path(tmp)
        shutil.copy(SERVER / "docker-compose.yaml", temp_server / "docker-compose.yaml")
        shutil.copy(UNRAID_COMPOSE_PATH, temp_server / "docker-compose.unraid.yaml")
        (temp_server / ".env").write_text("", encoding="utf-8")

        env = os.environ.copy()
        env.update(
            {
                "JWT_SECRET": "test-only-jwt-secret",
                "POSTGRES_PASSWORD": "test-only-postgres-password",
                "RAM0_HOST_IP": "192.168.1.126",
                "RAM0_API_IMAGE": "ghcr.io/olhapi/ram0-api@sha256:" + "a" * 64,
                "RAM0_DASHBOARD_IMAGE": "ghcr.io/olhapi/ram0-dashboard@sha256:"
                + "b" * 64,
                "RAM0_REVISION": "c" * 40,
                "RAM0_PUBLIC_API_URL": "https://api.example.test",
                "RAM0_DASHBOARD_URL": "https://dashboard.example.test",
            }
        )
        result = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(temp_server / "docker-compose.yaml"),
                "-f",
                str(temp_server / "docker-compose.unraid.yaml"),
                "config",
                "--format",
                "json",
            ],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )

    services = json.loads(result.stdout)["services"]
    assert services["mem0"]["image"] == env["RAM0_API_IMAGE"]
    assert services["mem0-dashboard"]["image"] == env["RAM0_DASHBOARD_IMAGE"]
    assert "build" not in services["mem0"]
    assert "build" not in services["mem0-dashboard"]
    assert services["postgres"].get("ports", []) == []
    assert services["mem0"]["ports"][0]["host_ip"] == env["RAM0_HOST_IP"]
    assert services["mem0-dashboard"]["ports"][0]["host_ip"] == env["RAM0_HOST_IP"]
    assert all(volume["target"] != "/app" for volume in services["mem0"]["volumes"])
