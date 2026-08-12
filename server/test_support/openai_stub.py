"""Deterministic OpenAI-compatible HTTP stub for container acceptance tests."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


_CATALOG_BEGIN = "BEGIN_UNTRUSTED_CATALOG\n"
_CATALOG_END = "\nEND_UNTRUSTED_CATALOG"
_MEMORY_BEGIN = "BEGIN_UNTRUSTED_MEMORY\n"
_MEMORY_END = "\nEND_UNTRUSTED_MEMORY"
_MAX_REQUEST_BYTES = 1_048_576
_malformed_attempts: dict[str, int] = {}
_malformed_attempts_lock = threading.Lock()


def _extract_delimited_json(text: str, begin: str, end: str) -> Any:
    """Decode JSON strictly between one pair of known prompt-data delimiters."""
    _, found, remainder = text.partition(begin)
    if not found:
        return None
    payload, found, _ = remainder.partition(end)
    if not found:
        return None
    try:
        return json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return None


def _allowed_names(user_text: str) -> list[str]:
    catalog = _extract_delimited_json(user_text, _CATALOG_BEGIN, _CATALOG_END)
    if not isinstance(catalog, list):
        return []

    names: list[str] = []
    for definition in catalog:
        if not isinstance(definition, dict):
            continue
        name = definition.get("name")
        if isinstance(name, str) and name and name not in names:
            names.append(name)
    return names


def _memory_text(user_text: str) -> str:
    memory_data = _extract_delimited_json(user_text, _MEMORY_BEGIN, _MEMORY_END)
    if not isinstance(memory_data, dict):
        return ""
    memory = memory_data.get("memory")
    return memory if isinstance(memory, str) else ""


def reset_malformed_attempts() -> None:
    """Reset retry state for direct tests; each container starts with empty state."""
    with _malformed_attempts_lock:
        _malformed_attempts.clear()


def classify_from_allowed_catalog(user_text: str) -> str:
    """Return deterministic classifier content using only the delimited catalog."""
    allowed = _allowed_names(user_text)
    memory = _memory_text(user_text)

    if "__CATEGORY_MALFORMED__" in memory:
        with _malformed_attempts_lock:
            attempt = _malformed_attempts.get(memory, 0) + 1
            _malformed_attempts[memory] = attempt
        if attempt <= 2:
            return "{invalid-json"

    if "__CATEGORY_NONE__" in memory:
        selected: list[str] = []
    elif "__CATEGORY_MULTI__" in memory:
        selected = allowed[:2]
    elif "__CATEGORY_UNKNOWN__" in memory:
        selected = ["invented_label"]
    elif "invoice" in memory.casefold() and "billing" in allowed:
        selected = ["billing"]
    else:
        selected = allowed[:1]

    return json.dumps({"categories": selected}, separators=(",", ":"))


def deterministic_embedding(value: Any) -> list[float]:
    """Create a stable, token-aware 1536-dimensional embedding without external packages."""
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    tokens = re.findall(r"[a-z0-9]+", serialized.casefold())
    vector = [0.0] * 1536
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:2], "big") % len(vector)
        vector[index] += 1.0 if digest[2] & 1 else -1.0
    norm = math.sqrt(sum(component * component for component in vector)) or 1.0
    return [round(component / norm, 8) for component in vector]


def _message_text(message: Any) -> str:
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            part.get("text", "") for part in content if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    return ""


class OpenAIStubHandler(BaseHTTPRequestHandler):
    """Serve the subset of the OpenAI API used by the Ram0 acceptance stack."""

    server_version = "Ram0OpenAIStub/1.0"

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path == "/health":
            self._send_json(HTTPStatus.OK, {"status": "ok"})
            return
        self._send_error(HTTPStatus.NOT_FOUND, "not_found", "Endpoint not found")

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        body = self._read_json_body()
        if body is None:
            return
        if self.path == "/v1/embeddings":
            self._send_embeddings(body)
        elif self.path == "/v1/chat/completions":
            self._send_chat_completion(body)
        else:
            self._send_error(HTTPStatus.NOT_FOUND, "not_found", "Endpoint not found")

    def _read_json_body(self) -> dict[str, Any] | None:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_error(HTTPStatus.BAD_REQUEST, "invalid_request_error", "Invalid content length")
            return None
        if content_length <= 0 or content_length > _MAX_REQUEST_BYTES:
            self._send_error(HTTPStatus.BAD_REQUEST, "invalid_request_error", "Invalid request size")
            return None
        try:
            body = json.loads(self.rfile.read(content_length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_error(HTTPStatus.BAD_REQUEST, "invalid_request_error", "Invalid JSON body")
            return None
        if not isinstance(body, dict):
            self._send_error(HTTPStatus.BAD_REQUEST, "invalid_request_error", "JSON body must be an object")
            return None
        return body

    def _send_embeddings(self, body: dict[str, Any]) -> None:
        inputs = body.get("input", [])
        if not isinstance(inputs, list):
            inputs = [inputs]
        data = [
            {"object": "embedding", "embedding": deterministic_embedding(value), "index": index}
            for index, value in enumerate(inputs)
        ]
        self._send_json(
            HTTPStatus.OK,
            {
                "object": "list",
                "data": data,
                "model": body.get("model", "ram0-embedding-stub"),
                "usage": {"prompt_tokens": 0, "total_tokens": 0},
            },
        )

    def _send_chat_completion(self, body: dict[str, Any]) -> None:
        messages = body.get("messages", [])
        if not isinstance(messages, list):
            messages = []
        system_text = "\n".join(
            _message_text(message)
            for message in messages
            if isinstance(message, dict) and message.get("role") == "system"
        )
        user_text = "\n".join(
            _message_text(message)
            for message in messages
            if isinstance(message, dict) and message.get("role") == "user"
        )
        if "RAM0_CATEGORY_CLASSIFIER_V1" in system_text:
            content = classify_from_allowed_catalog(user_text)
        else:
            content = json.dumps({"memory": [{"id": "0", "text": "The invoice is ready"}]})

        self._send_json(
            HTTPStatus.OK,
            {
                "id": "chatcmpl-ram0-stub",
                "object": "chat.completion",
                "created": 0,
                "model": body.get("model", "ram0-chat-stub"),
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            },
        )

    def _send_error(self, status: HTTPStatus, error_type: str, message: str) -> None:
        self._send_json(status, {"error": {"message": message, "type": error_type}})

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: Any) -> None:
        """Avoid logging request data or headers used by prompt-injection tests."""


def main() -> None:
    host = os.environ.get("OPENAI_STUB_HOST", "0.0.0.0")
    port = int(os.environ.get("OPENAI_STUB_PORT", "8080"))
    server = ThreadingHTTPServer((host, port), OpenAIStubHandler)
    print(f"Ram0 OpenAI stub listening on {host}:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
