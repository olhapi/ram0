"""Process-wide test isolation shared by legacy and server-focused suites."""

import sys
from pathlib import Path

import pytest


SERVER_DIR = str(Path(__file__).resolve().parents[1] / "server")
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)


@pytest.fixture(autouse=True)
def _isolate_reloaded_server_modules():
    """Prevent reload-based REST fixtures from leaking runtime singletons by test order."""
    yield

    runtime = sys.modules.get("category_runtime")
    worker = getattr(runtime, "_worker", None) if runtime is not None else None
    if worker is not None:
        try:
            worker.stop()
        except Exception:
            pass
    if runtime is not None:
        runtime._service = None
        runtime._worker = None
    sys.modules.pop("server.main", None)
    sys.modules.pop("auth", None)
