"""Real protocol-boundary tests for the config-aware stdio MCP adapter."""

from __future__ import annotations

import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from mcp_stdio_adapter import StreamableHttpTransport, run_stdio
from ram0_config import write_config


INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
}
TOOLS_LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}


class StreamableMcpFixture:
    def __init__(self):
        self.requests: list[dict[str, object]] = []
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def api_url(self) -> str:
        assert self._server is not None
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def __enter__(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def _record(self, body=None):
                owner.requests.append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "headers": {key.lower(): value for key, value in self.headers.items()},
                        "body": body,
                    }
                )

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length))
                self._record(body)
                if body.get("method") == "notifications/initialized":
                    self.send_response(202)
                    self.end_headers()
                    return
                response = {
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {"protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "ram0", "version": "1"}}
                    if body.get("method") == "initialize"
                    else {"tools": [{"name": "remember"}]},
                }
                encoded = json.dumps(response).encode()
                self.send_response(200)
                if body.get("method") == "initialize":
                    self.send_header("Mcp-Session-Id", "session-1")
                    self.send_header("Content-Type", "text/event-stream")
                    encoded = f"event: message\ndata: {json.dumps(response)}\n\n".encode()
                else:
                    self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def do_GET(self):
                self._record()
                notification = {"jsonrpc": "2.0", "method": "notifications/message", "params": {"level": "info"}}
                encoded = f"event: message\ndata: {json.dumps(notification)}\n\n".encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def do_DELETE(self):
                self._record()
                self.send_response(204)
                self.end_headers()

            def log_message(self, _format, *_args):
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_args):
        assert self._server is not None and self._thread is not None
        self._server.shutdown()
        self._server.server_close()
        self._thread.join()


def test_transport_negotiates_session_and_forwards_json_and_sse_without_changing_rpc():
    """Breaks if the adapter loses JSON-RPC bodies, session state, protocol headers, or response formats."""
    with StreamableMcpFixture() as server:
        transport = StreamableHttpTransport(f"{server.api_url}/mcp", "adapter-key")
        assert transport.send(INITIALIZE)[0]["id"] == 1
        assert transport.send({"jsonrpc": "2.0", "method": "notifications/initialized"}) == []
        assert transport.send(TOOLS_LIST) == [{"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": "remember"}]}}]
        transport.close()

    posts = [request for request in server.requests if request["method"] == "POST"]
    assert all(request["path"] == "/mcp/" for request in server.requests)
    assert [request["body"] for request in posts] == [INITIALIZE, {"jsonrpc": "2.0", "method": "notifications/initialized"}, TOOLS_LIST]
    assert all(request["headers"]["authorization"] == "Bearer adapter-key" for request in server.requests)
    assert posts[1]["headers"]["mcp-session-id"] == "session-1"
    assert posts[2]["headers"]["mcp-protocol-version"] == "2025-06-18"
    assert server.requests[-1]["method"] == "DELETE"


def test_transport_listen_emits_remote_notification_unchanged():
    """Breaks if server-originated notifications disappear or are rewritten."""
    with StreamableMcpFixture() as server:
        transport = StreamableHttpTransport(f"{server.api_url}/mcp", "adapter-key")
        transport.send(INITIALIZE)
        emitted: list[dict] = []
        transport.listen(emitted.append, threading.Event())
        transport.close()
    assert emitted == [{"jsonrpc": "2.0", "method": "notifications/message", "params": {"level": "info"}}]


def test_run_stdio_loads_persistent_config_and_redacts_failures(tmp_path):
    """Breaks if a clean process needs exported variables or diagnostics disclose the stored key."""
    with StreamableMcpFixture() as server:
        write_config(server.api_url, "stored-adapter-key", home=tmp_path)
        stdout, stderr = io.StringIO(), io.StringIO()
        code = run_stdio(
            io.StringIO(json.dumps(INITIALIZE) + "\n" + json.dumps(TOOLS_LIST) + "\n"),
            stdout,
            stderr,
            environment={},
            home=tmp_path,
        )
    messages = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert code == 0
    assert [message.get("id") for message in messages if "id" in message] == [1, 2]
    assert "stored-adapter-key" not in stdout.getvalue() + stderr.getvalue()


def test_run_stdio_uses_the_canonical_mcp_mount_path(tmp_path):
    """Breaks if POST requests rely on a 307 redirect that standard clients will not replay."""
    endpoints: list[str] = []

    class RecordingTransport:
        def __init__(self, endpoint, _api_key):
            endpoints.append(endpoint)

        def send(self, _message):
            return []

        def close(self):
            return None

    write_config("https://ram0.example.test/base/", "stored-adapter-key", home=tmp_path)
    assert run_stdio(io.StringIO(""), io.StringIO(), io.StringIO(), environment={}, home=tmp_path,
                     transport_factory=RecordingTransport) == 0
    assert endpoints == ["https://ram0.example.test/base/mcp/"]


def test_run_stdio_reports_invalid_input_without_stopping_later_requests(tmp_path):
    """Breaks if one malformed client line kills the transport or echoes secret-bearing input."""
    with StreamableMcpFixture() as server:
        write_config(server.api_url, "stored-adapter-key", home=tmp_path)
        stdout, stderr = io.StringIO(), io.StringIO()
        code = run_stdio(io.StringIO("not-json stored-adapter-key\n" + json.dumps(TOOLS_LIST) + "\n"), stdout, stderr,
                         environment={}, home=tmp_path)
    assert code == 0
    assert json.loads(stdout.getvalue())["id"] == 2
    assert "invalid JSON-RPC input" in stderr.getvalue()
    assert "stored-adapter-key" not in stderr.getvalue()
