#!/usr/bin/env python3
"""Run the Ram0 plugin acceptance test against an isolated Docker stack."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


SERVER_DIR = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = SERVER_DIR.parent
TEST_FILE = REPOSITORY_ROOT / "tests" / "server" / "test_e2e_ram0_plugin.py"
PINNED_PGVECTOR_IMAGE = "pgvector/pgvector:pg17@sha256:7ae6051efd0e60444282c27c7e141af07f322ce033300e727a49c3dd11075e38"
_DIGEST_ROOTS = (REPOSITORY_ROOT / "mem0", SERVER_DIR)
_DIGEST_FILES = (REPOSITORY_ROOT / "pyproject.toml", REPOSITORY_ROOT / "poetry.lock", REPOSITORY_ROOT / "README.md")
_IGNORED_DIRECTORY_NAMES = {
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "history",
    "htmlcov",
    "node_modules",
}


def _run(command: list[str], *, env: dict[str, str] | None = None, capture: bool = False) -> str:
    completed = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        env=env,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )
    return completed.stdout.strip() if capture else ""


def _require_tool(name: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(f"required tool is unavailable: {name}")


def _image_exists(image: str) -> bool:
    try:
        _run(["docker", "image", "inspect", image], capture=True)
    except subprocess.CalledProcessError:
        return False
    return True


def _source_digest() -> str:
    digest = hashlib.sha256()
    for path in _DIGEST_FILES:
        digest.update(str(path.relative_to(REPOSITORY_ROOT)).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    for root in _DIGEST_ROOTS:
        for directory, directory_names, file_names in os.walk(root):
            directory_path = Path(directory)
            directory_names[:] = sorted(
                name
                for name in directory_names
                if name not in _IGNORED_DIRECTORY_NAMES and not (directory_path == SERVER_DIR and name == "dashboard")
            )
            for name in sorted(file_names):
                path = directory_path / name
                if name.startswith(".env") or path.suffix in {".pyc", ".pyo"}:
                    continue
                digest.update(str(path.relative_to(REPOSITORY_ROOT)).encode())
                digest.update(b"\0")
                digest.update(path.read_bytes())
                digest.update(b"\0")
    return digest.hexdigest()


def _image_names(source_digest: str) -> dict[str, str]:
    tag = source_digest[:16]
    return {
        "api": f"ram0-plugin-e2e-api:{tag}",
        "stub": f"ram0-plugin-e2e-stub:{tag}",
        "runner": f"ram0-plugin-e2e-runner:{tag}",
    }


def _image_has_digest(image: str, source_digest: str) -> bool:
    if not _image_exists(image):
        return False
    label = _run(
        ["docker", "image", "inspect", image, "--format", '{{index .Config.Labels "ram0.e2e.source-digest"}}'],
        capture=True,
    )
    return label == source_digest


def _build_images(images: dict[str, str], source_digest: str, temporary_directory: Path) -> None:
    common = ["--pull=false", "--label", f"ram0.e2e.source-digest={source_digest}"]
    if not _image_has_digest(images["api"], source_digest):
        _run(
            [
                "docker",
                "build",
                *common,
                "--build-arg",
                f"VCS_REF=e2e-{source_digest}",
                "--tag",
                images["api"],
                "--file",
                str(SERVER_DIR / "Dockerfile"),
                str(REPOSITORY_ROOT),
            ]
        )
    if not _image_has_digest(images["stub"], source_digest):
        _run(
            [
                "docker",
                "build",
                *common,
                "--tag",
                images["stub"],
                "--file",
                str(SERVER_DIR / "test-support.Dockerfile"),
                str(SERVER_DIR),
            ]
        )
    if not _image_has_digest(images["runner"], source_digest):
        runner_dockerfile = temporary_directory / "runner.Dockerfile"
        runner_dockerfile.write_text(
            f"FROM {images['api']}\n"
            "RUN pip install --no-cache-dir pytest==9.1.1 pytest-asyncio==1.4.0 pytest-mock==3.15.1\n"
            "WORKDIR /workspace\n"
        )
        _run(
            [
                "docker",
                "build",
                *common,
                "--tag",
                images["runner"],
                "--file",
                str(runner_dockerfile),
                str(temporary_directory),
            ]
        )


def _ensure_pgvector_image() -> None:
    if not _image_exists(PINNED_PGVECTOR_IMAGE):
        _run(["docker", "pull", PINNED_PGVECTOR_IMAGE])
    if not _image_exists(PINNED_PGVECTOR_IMAGE):
        raise RuntimeError("the pinned pgvector test image is unavailable after preflight")


def _require_prepared_images(images: dict[str, str], source_digest: str) -> None:
    missing = []
    if not _image_exists(PINNED_PGVECTOR_IMAGE):
        missing.append(PINNED_PGVECTOR_IMAGE)
    for image in images.values():
        if not _image_has_digest(image, source_digest):
            missing.append(image)
    if missing:
        joined = ", ".join(missing)
        raise RuntimeError(
            f"offline Ram0 plugin e2e prerequisites are missing or stale: {joined}; "
            "run `make -C server e2e-ram0-plugin-prepare` while network access is available"
        )


def _compose_config(images: dict[str, str], repository_root: Path = REPOSITORY_ROOT) -> dict[str, object]:
    return {
        "name": "ram0-plugin-e2e",
        "networks": {"default": {"internal": True}},
        "volumes": {"postgres_data": {}, "api_history": {}},
        "services": {
            "postgres": {
                "image": PINNED_PGVECTOR_IMAGE,
                "pull_policy": "never",
                "environment": {
                    "POSTGRES_USER": "postgres",
                    "POSTGRES_PASSWORD": "ram0-test-only",
                    "POSTGRES_DB": "postgres",
                },
                "volumes": [
                    "postgres_data:/var/lib/postgresql/data",
                    f"{SERVER_DIR / 'init-db.sh'}:/docker-entrypoint-initdb.d/init-db.sh:ro",
                ],
                "healthcheck": {
                    "test": ["CMD-SHELL", "pg_isready -q -d postgres -U postgres"],
                    "interval": "2s",
                    "timeout": "5s",
                    "retries": 30,
                },
            },
            "openai-stub": {
                "image": images["stub"],
                "pull_policy": "never",
                "healthcheck": {
                    "test": [
                        "CMD",
                        "python",
                        "-c",
                        "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2).read()",
                    ],
                    "interval": "2s",
                    "timeout": "5s",
                    "retries": 30,
                },
            },
            "ram0-api": {
                "image": images["api"],
                "pull_policy": "never",
                "environment": {
                    "OPENAI_API_KEY": "test-key",
                    "OPENAI_BASE_URL": "http://openai-stub:8080/v1",
                    "AUTH_DISABLED": "false",
                    "JWT_SECRET": "ram0-plugin-e2e-only-jwt-secret-not-for-production",
                    "MEM0_TELEMETRY": "false",
                    "CATEGORY_WORKER_ENABLED": "false",
                    "POSTGRES_HOST": "postgres",
                    "POSTGRES_PORT": "5432",
                    "POSTGRES_DB": "postgres",
                    "POSTGRES_USER": "postgres",
                    "POSTGRES_PASSWORD": "ram0-test-only",
                    "APP_DB_NAME": "mem0_app",
                    "HISTORY_DB_PATH": "/app/history/history.db",
                    "DASHBOARD_URL": "http://dashboard.invalid",
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONUNBUFFERED": "1",
                },
                "volumes": ["api_history:/app/history"],
                "depends_on": {
                    "postgres": {"condition": "service_healthy"},
                    "openai-stub": {"condition": "service_healthy"},
                },
                "healthcheck": {
                    "test": [
                        "CMD",
                        "python",
                        "-c",
                        "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/docs', timeout=2).read()",
                    ],
                    "interval": "2s",
                    "timeout": "5s",
                    "retries": 60,
                },
            },
            "e2e-runner": {
                "image": images["runner"],
                "pull_policy": "never",
                "working_dir": "/workspace",
                "environment": {
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "RAM0_E2E_API_URL": "http://ram0-api:8000",
                    "RAM0_E2E_DATABASE_URL": "postgresql://postgres:ram0-test-only@postgres:5432/mem0_app",
                },
                "volumes": [f"{repository_root}:/workspace:ro"],
                "command": [
                    "python",
                    "-m",
                    "pytest",
                    "-q",
                    "-p",
                    "no:cacheprovider",
                    str(TEST_FILE.relative_to(REPOSITORY_ROOT)),
                ],
            },
        },
    }


def _assert_runtime_isolated(compose: list[str]) -> None:
    network_id = _run([*compose, "ps", "-q", "postgres"], capture=True)
    if not network_id:
        raise RuntimeError("the isolated PostgreSQL service is not running")
    project_network = _run(
        [
            "docker",
            "inspect",
            network_id,
            "--format",
            "{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{end}}",
        ],
        capture=True,
    )
    internal = _run(["docker", "network", "inspect", project_network, "--format", "{{.Internal}}"], capture=True)
    if internal != "true":
        raise RuntimeError("the e2e Compose network is not internal")
    for service in ("postgres", "openai-stub", "ram0-api"):
        container_id = _run([*compose, "ps", "-q", service], capture=True)
        bindings = _run(
            ["docker", "inspect", container_id, "--format", "{{json .HostConfig.PortBindings}}"], capture=True
        )
        if bindings not in {"null", "{}"}:
            raise RuntimeError(f"the {service} test service unexpectedly publishes a host port")


def _run_offline_stack(images: dict[str, str], temporary_path: Path, project: str) -> None:
    compose_file = temporary_path / "compose.json"
    compose_file.write_text(json.dumps(_compose_config(images)))
    compose = ["docker", "compose", "-p", project, "-f", str(compose_file)]
    stack_touched = False
    try:
        stack_touched = True
        _run(
            [
                *compose,
                "up",
                "-d",
                "--no-build",
                "--pull",
                "never",
                "--wait",
                "postgres",
                "openai-stub",
                "ram0-api",
            ]
        )
        _assert_runtime_isolated(compose)
        _run([*compose, "run", "--rm", "--no-deps", "--pull", "never", "e2e-runner"])
    finally:
        if stack_touched:
            _run([*compose, "down", "--volumes", "--remove-orphans"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("prepare", "run"), nargs="?", default="run")
    args = parser.parse_args()
    _require_tool("docker")
    source_digest = _source_digest()
    images = _image_names(source_digest)

    with tempfile.TemporaryDirectory(prefix="ram0-plugin-e2e-") as temporary_directory:
        temporary_path = Path(temporary_directory)
        if args.operation == "prepare":
            _ensure_pgvector_image()
            _build_images(images, source_digest, temporary_path)
            _require_prepared_images(images, source_digest)
            print("Ram0 plugin e2e images are prepared for offline runs.")
            return 0
        _require_prepared_images(images, source_digest)
        _run_offline_stack(images, temporary_path, f"ram0-plugin-e2e-{os.getpid()}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Ram0 plugin e2e failed: {error}", file=sys.stderr)
        raise SystemExit(1) from None
