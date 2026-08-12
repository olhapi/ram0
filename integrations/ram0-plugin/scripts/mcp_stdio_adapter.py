#!/usr/bin/env python3
"""Bridge MCP JSON-RPC over stdio to Ram0 Streamable HTTP."""

from __future__ import annotations

import json
import os
import sys
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, TextIO
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ram0_config import Ram0ConfigError, load_config


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _sse_messages(payload: bytes) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    data_lines: list[str] = []
    for line in payload.decode("utf-8").splitlines() + [""]:
        if line == "":
            if data_lines:
                value = json.loads("\n".join(data_lines))
                if isinstance(value, dict):
                    messages.append(value)
            data_lines = []
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    return messages


class StreamableHttpTransport:
    def __init__(self, endpoint: str, api_key: str, *, timeout: float = 30) -> None:
        self.endpoint = endpoint.rstrip("/") + "/"
        self._api_key = api_key
        self._timeout = timeout
        self._session_id: str | None = None
        self._protocol_version: str | None = None
        self._opener = build_opener(_NoRedirectHandler)

    def _headers(self, *, content: bool = False) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json, text/event-stream",
        }
        if content:
            headers["Content-Type"] = "application/json"
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        if self._protocol_version:
            headers["MCP-Protocol-Version"] = self._protocol_version
        return headers

    @staticmethod
    def _decode(payload: bytes, content_type: str) -> list[dict[str, Any]]:
        if not payload:
            return []
        if content_type.split(";", 1)[0].strip().lower() == "text/event-stream":
            return _sse_messages(payload)
        value = json.loads(payload)
        if isinstance(value, dict):
            return [value]
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        raise ValueError("MCP response must contain a JSON object or array.")

    def send(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        if message.get("method") == "initialize":
            version = message.get("params", {}).get("protocolVersion")
            if isinstance(version, str):
                self._protocol_version = version
        request = Request(
            self.endpoint,
            data=json.dumps(message, separators=(",", ":")).encode(),
            headers=self._headers(content=True),
            method="POST",
        )
        with self._opener.open(request, timeout=self._timeout) as response:
            session_id = response.headers.get("Mcp-Session-Id")
            if session_id:
                self._session_id = session_id
            return self._decode(response.read(), response.headers.get("Content-Type", "application/json"))

    def listen(self, emit: Callable[[dict[str, Any]], None], stop: threading.Event) -> None:
        if not self._session_id or stop.is_set():
            return
        request = Request(self.endpoint, headers=self._headers(), method="GET")
        with self._opener.open(request, timeout=self._timeout) as response:
            for message in self._decode(response.read(), response.headers.get("Content-Type", "text/event-stream")):
                if stop.is_set():
                    return
                emit(message)

    def close(self) -> None:
        if not self._session_id:
            return
        request = Request(self.endpoint, headers=self._headers(), method="DELETE")
        try:
            with self._opener.open(request, timeout=self._timeout):
                pass
        finally:
            self._session_id = None


def _redacted_message(error: Exception, api_key: str) -> str:
    value = str(error).replace(api_key, "[redacted credential]") if api_key else str(error)
    return value.replace("Authorization", "[redacted header]")


def run_stdio(
    stdin: TextIO,
    stdout: TextIO,
    stderr: TextIO,
    *,
    environment: Mapping[str, str] | None = None,
    home: Path | None = None,
    transport_factory=StreamableHttpTransport,
) -> int:
    try:
        config = load_config(environment, home=home, require_key=True)
    except Ram0ConfigError as error:
        print(str(error), file=stderr, flush=True)
        return 1
    transport = transport_factory(f"{config.api_url}/mcp/", config.api_key)
    output_lock = threading.Lock()
    stop = threading.Event()
    listener: threading.Thread | None = None

    def emit(message: dict[str, Any]) -> None:
        with output_lock:
            stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
            stdout.flush()

    try:
        for line in stdin:
            try:
                message = json.loads(line)
                if not isinstance(message, dict):
                    raise ValueError
            except (ValueError, json.JSONDecodeError):
                print("Ram0 MCP adapter received invalid JSON-RPC input.", file=stderr, flush=True)
                continue
            try:
                for response in transport.send(message):
                    emit(response)
                if message.get("method") == "initialize" and listener is None:
                    listener = threading.Thread(target=transport.listen, args=(emit, stop), daemon=True)
                    listener.start()
            except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError) as error:
                print(f"Ram0 MCP transport unavailable: {_redacted_message(error, config.api_key)}", file=stderr, flush=True)
        if listener is not None:
            listener.join(timeout=1)
        return 0
    finally:
        stop.set()
        try:
            transport.close()
        except (HTTPError, URLError, OSError):
            pass


if __name__ == "__main__":
    raise SystemExit(run_stdio(sys.stdin, sys.stdout, sys.stderr, environment=os.environ))
