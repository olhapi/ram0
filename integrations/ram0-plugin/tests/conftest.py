"""Local HTTP fixtures for Ram0 plugin contract tests."""

from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


class RecordingRam0Server:
    """A real local HTTP server that records the adapter boundary."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.status = 200
        self.response: Any = {"results": []}
        self.response_headers: dict[str, str] = {}
        self.delay_seconds = 0.0
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        assert self._server is not None
        host, port = self._server.server_address
        return f"http://{host}:{port}/"

    def __enter__(self) -> "RecordingRam0Server":
        recorder = self

        class Handler(BaseHTTPRequestHandler):
            def _record(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                recorder.requests.append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "headers": {key.lower(): value for key, value in self.headers.items()},
                        "body": self.rfile.read(length).decode("utf-8") if length else None,
                    }
                )
                if recorder.delay_seconds:
                    time.sleep(recorder.delay_seconds)
                encoded = json.dumps(recorder.response).encode("utf-8")
                self.send_response(recorder.status)
                self.send_header("Content-Type", "application/json")
                for key, value in recorder.response_headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            do_GET = _record
            do_POST = _record
            do_PUT = _record
            do_DELETE = _record

            def log_message(self, _format: str, *_args: object) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        assert self._server is not None
        self._server.shutdown()
        self._server.server_close()
        assert self._thread is not None
        self._thread.join()


@pytest.fixture()
def ram0_server() -> RecordingRam0Server:
    with RecordingRam0Server() as server:
        yield server
