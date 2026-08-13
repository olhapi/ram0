# Modified for Ram0; see NOTICE and repository history.
"""Small, account-derived REST adapter for the self-hosted Ram0 service."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ram0_config import RAM0_USER_AGENT


DEFAULT_TIMEOUT_SECONDS = 10.0
_RESERVED_PAYLOAD_KEYS = {
    "user_id",
    "app_id",
    "run_id",
    "expiration_date",
    "api_key",
    "api_token",
    "access_token",
    "authorization",
    "credential",
    "credentials",
    "token",
    "secret",
    "secret_key",
    "password",
    "db_password",
}
_RESERVED_CREDENTIAL_SUFFIXES = ("_token", "_secret", "_password", "_credentials")
_FILTER_RESERVED_KEYS = _RESERVED_PAYLOAD_KEYS - {"user_id", "app_id", "run_id", "expiration_date"}
_APP_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SCOPES = {None, "project", "global"}
_MAX_FILTER_DEPTH = 64


class _NoRedirectHandler(HTTPRedirectHandler):
    """Reject redirects before urllib can forward Ram0's bearer credential."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        return None


_NO_REDIRECT_OPENER = build_opener(_NoRedirectHandler)


def _open_request(request: Request, *, timeout: float):
    return _NO_REDIRECT_OPENER.open(request, timeout=timeout)


@dataclass(frozen=True)
class Ram0ClientError(Exception):
    """A safe failure callers can display without exposing server or request data."""

    status: int | None
    code: str
    action: str

    def __str__(self) -> str:
        status = str(self.status) if self.status is not None else "network"
        return f"Ram0 request failed ({status} {self.code}). {self.action}"


class Ram0Client:
    """Own the complete HTTP seam for an authenticated Ram0 account."""

    def __init__(self, api_url: str, api_key: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self._api_url = self._normalize_url(api_url)
        normalized_api_key = api_key.strip()
        if not normalized_api_key:
            raise ValueError("RAM0_API_KEY is required for network operations.")
        self._api_key = normalized_api_key
        self._timeout = timeout

    def search(
        self,
        query: str,
        limit: int = 10,
        *,
        app_id: str,
        scope: str | None = None,
        filters: Mapping[str, Any] | None = None,
    ) -> Any:
        body = {"query": query, "top_k": self._limit(limit)}
        if (read_filters := self._read_filters(app_id, scope, filters)) is not None:
            body["filters"] = read_filters
        return self._request("POST", "/search", body, trusted_filter_context=True)

    def add(
        self,
        memory_text: str,
        metadata: Mapping[str, Any] | None = None,
        *,
        app_id: str | None,
        scope: str | None = None,
    ) -> Any:
        safe_metadata = self._safe_metadata(metadata)
        body = {"messages": [{"role": "user", "content": memory_text}], "metadata": safe_metadata}
        if (write_app_id := self._write_app_id(app_id, scope)) is not None:
            body["app_id"] = write_app_id
        return self._request(
            "POST",
            "/memories",
            body,
            trusted_app_context=True,
        )

    def add_durable(
        self,
        memory_text: str,
        metadata: Mapping[str, Any] | None = None,
        *,
        app_id: str | None,
        scope: str | None = None,
    ) -> Any:
        """Persist an already-selected durable candidate without server inference."""
        safe_metadata = self._safe_metadata(metadata)
        body = {
            "messages": [{"role": "user", "content": memory_text}],
            "metadata": safe_metadata,
            "infer": False,
        }
        if (write_app_id := self._write_app_id(app_id, scope)) is not None:
            body["app_id"] = write_app_id
        return self._request(
            "POST",
            "/memories",
            body,
            trusted_app_context=True,
        )

    def list(
        self,
        limit: int = 100,
        *,
        app_id: str,
        scope: str | None = None,
        filters: Mapping[str, Any] | None = None,
    ) -> Any:
        query: dict[str, Any] = {"top_k": self._limit(limit)}
        if (read_filters := self._read_filters(app_id, scope, filters)) is not None:
            query["filters"] = json.dumps(read_filters, separators=(",", ":"))
        return self._request("GET", "/memories", query=query)

    def get(self, memory_id: str) -> Any:
        return self._request("GET", self._memory_path(memory_id))

    def update(self, memory_id: str, memory_text: str, metadata: Mapping[str, Any] | None = None) -> Any:
        safe_metadata = self._safe_metadata(metadata)
        return self._request("PUT", self._memory_path(memory_id), {"text": memory_text, "metadata": safe_metadata})

    def delete(self, memory_id: str) -> Any:
        return self._request("DELETE", self._memory_path(memory_id))

    def get_categories(self) -> Any:
        return self._request("GET", "/categories")

    def create_category(self, definition: Mapping[str, Any]) -> Any:
        return self._request("POST", "/categories", dict(definition))

    def put_categories(self, definitions: Sequence[Mapping[str, Any]]) -> Any:
        return self._request("PUT", "/categories", list(definitions))

    @staticmethod
    def _normalize_url(value: str) -> str:
        parsed = urlsplit(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("RAM0_API_URL must be an absolute HTTP(S) URL.")
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))

    @staticmethod
    def _limit(value: int) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError("limit must be a positive integer.")
        return value

    @staticmethod
    def _scope(value: str | None) -> str | None:
        if value not in _SCOPES:
            raise ValueError('scope must be omitted, "project", or "global".')
        return value

    @classmethod
    def _project_app_id(cls, value: str | None) -> str:
        if not isinstance(value, str) or _APP_ID.fullmatch(value) is None:
            raise ValueError("app_id must be a non-empty normalized project identifier.")
        return value

    @classmethod
    def _read_filters(
        cls,
        app_id: str | None,
        scope: str | None,
        filters: Mapping[str, Any] | None,
    ) -> dict[str, Any] | None:
        selected_scope = cls._scope(scope)
        caller_filters = cls._safe_filters(filters)
        if selected_scope == "global":
            scope_filter = None
        else:
            current = cls._project_app_id(app_id)
            if selected_scope == "project":
                scope_filter = {"app_id": current}
            else:
                scope_filter = {"OR": [{"app_id": current}, {"app_id": None}]}
        if scope_filter is not None and caller_filters:
            return {"AND": [scope_filter, caller_filters]}
        return scope_filter or caller_filters or None

    @classmethod
    def _safe_filters(cls, filters: Mapping[str, Any] | None) -> dict[str, Any]:
        if filters is None:
            return {}
        if not isinstance(filters, Mapping):
            raise ValueError("filters must be a mapping.")
        copied = dict(filters)
        cls._validate_filters(copied)
        return copied

    @classmethod
    def _validate_filters(cls, filters: Mapping[str, Any]) -> None:
        pending = [(filters, 0, False)]
        while pending:
            current, depth, app_value = pending.pop()
            if depth > _MAX_FILTER_DEPTH:
                raise ValueError("filters are too deeply nested.")
            if isinstance(current, Mapping):
                for key in current:
                    normalized = str(key).strip().lower().replace("-", "_")
                    if normalized == "user_id":
                        raise ValueError("filter key 'user_id' is reserved.")
                    if normalized in _FILTER_RESERVED_KEYS or normalized.endswith(_RESERVED_CREDENTIAL_SUFFIXES):
                        raise ValueError(f"filter key '{key}' is reserved.")
            if app_value:
                if current is None:
                    continue
                if isinstance(current, str):
                    cls._project_app_id(current)
                    continue
                if isinstance(current, Mapping):
                    pending.extend((nested, depth + 1, True) for nested in current.values())
                    continue
                if isinstance(current, Sequence) and not isinstance(current, (str, bytes, bytearray)):
                    pending.extend((nested, depth + 1, True) for nested in current)
                    continue
                raise ValueError("app_id contains an invalid project identifier.")
            if isinstance(current, Mapping):
                for key, nested in current.items():
                    normalized = str(key).strip().lower().replace("-", "_")
                    pending.append((nested, depth + 1, normalized == "app_id"))
            elif isinstance(current, Sequence) and not isinstance(current, (str, bytes, bytearray)):
                pending.extend((nested, depth + 1, False) for nested in current)

    @classmethod
    def _write_app_id(cls, app_id: str | None, scope: str | None) -> str | None:
        selected_scope = cls._scope(scope)
        if selected_scope == "global":
            return None
        return cls._project_app_id(app_id)

    @staticmethod
    def _memory_path(memory_id: str) -> str:
        if not memory_id:
            raise ValueError("memory_id is required.")
        return f"/memories/{quote(str(memory_id), safe='')}"

    @classmethod
    def _safe_metadata(cls, metadata: Mapping[str, Any] | None) -> dict[str, Any]:
        if metadata is None:
            return {}
        if not isinstance(metadata, Mapping):
            raise ValueError("metadata must be a mapping.")
        copied = dict(metadata)
        cls._reject_reserved_keys(copied)
        return copied

    @classmethod
    def _reject_reserved_keys(
        cls,
        value: Any,
        *,
        allow_top_level_app_id: bool = False,
        _depth: int = 0,
    ) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                normalized = str(key).strip().lower().replace("-", "_")
                allowed_app_id = allow_top_level_app_id and _depth == 0 and normalized == "app_id"
                if not allowed_app_id and (
                    normalized in _RESERVED_PAYLOAD_KEYS or normalized.endswith(_RESERVED_CREDENTIAL_SUFFIXES)
                ):
                    raise ValueError(f"payload key '{key}' is reserved.")
                cls._reject_reserved_keys(
                    child,
                    allow_top_level_app_id=allow_top_level_app_id,
                    _depth=_depth + 1,
                )
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            for child in value:
                cls._reject_reserved_keys(
                    child,
                    allow_top_level_app_id=allow_top_level_app_id,
                    _depth=_depth + 1,
                )

    def _request(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        query: Mapping[str, Any] | None = None,
        trusted_app_context: bool = False,
        trusted_filter_context: bool = False,
    ) -> Any:
        url = f"{self._api_url}{path}"
        if query:
            url = f"{url}?{urlencode(query)}"
        if body is not None and not trusted_filter_context:
            self._reject_reserved_keys(
                body,
                allow_top_level_app_id=trusted_app_context,
            )
        data = json.dumps(body, separators=(",", ":")).encode("utf-8") if body is not None else None
        headers = {"Authorization": f"Bearer {self._api_key}", "User-Agent": RAM0_USER_AGENT}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with _open_request(request, timeout=self._timeout) as response:
                raw = response.read()
        except HTTPError as error:
            raise self._http_error(error.code) from None
        except (URLError, TimeoutError, OSError):
            raise Ram0ClientError(None, "network_error", "Check RAM0_API_URL and network connectivity.") from None

        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise Ram0ClientError(None, "invalid_response", "Check the Ram0 server response and try again.") from None

    @staticmethod
    def _http_error(status: int) -> Ram0ClientError:
        if status in {401, 403}:
            return Ram0ClientError(status, "unauthorized", "Check RAM0_API_KEY and its account access.")
        if status == 404:
            return Ram0ClientError(status, "not_found", "Check the Ram0 API URL and requested resource.")
        if status == 429:
            return Ram0ClientError(status, "rate_limited", "Wait briefly and try again.")
        if status >= 500:
            return Ram0ClientError(status, "service_unavailable", "Check RAM0_API_URL and try again.")
        return Ram0ClientError(status, "request_rejected", "Check the request and Ram0 server configuration.")
