# Modified for Ram0; see NOTICE and repository history.
"""Immutable ownership policy for account-scoped core-memory routes."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from auth import require_auth
from app_scope import validate_app_id
from fastapi import Depends, HTTPException
from memory_owner_migration import require_ownership_ready
from models import User

from mem0.vector_stores.pgvector import OutputData


_MAX_CLIENT_STRUCTURE_DEPTH = 64


@dataclass(frozen=True, slots=True)
class MemoryPrincipal:
    """The sole account identity allowed to reach the memory engine."""

    owner_id: str


def principal_for(user: User) -> MemoryPrincipal:
    """Derive the memory owner from the authenticated account UUID."""
    return MemoryPrincipal(owner_id=str(user.id))


def require_memory_principal(user: User = Depends(require_auth)) -> MemoryPrincipal:
    """Require an authenticated account and a completed ownership migration."""
    require_ownership_ready()
    return principal_for(user)


def reject_client_owner(value: object) -> None:
    """Reject client-controlled owner or project selectors in nested request data."""
    pending = [(value, 0)]
    while pending:
        current, depth = pending.pop()
        if depth > _MAX_CLIENT_STRUCTURE_DEPTH:
            raise HTTPException(status_code=422, detail="Request structure is too deeply nested.")
        if isinstance(current, Mapping):
            if "user_id" in current:
                raise HTTPException(status_code=422, detail="user_id is assigned from the authenticated account.")
            if "app_id" in current:
                raise HTTPException(status_code=422, detail="app_id is assigned from the trusted project context.")
            pending.extend((nested_value, depth + 1) for nested_value in current.values())
        elif isinstance(current, Sequence) and not isinstance(current, (str, bytes)):
            pending.extend((nested_value, depth + 1) for nested_value in current)


def _validate_structured_filters(value: object) -> None:
    """Validate app predicates and reject owner predicates at any safe structure depth."""
    pending = [(value, 0, False)]
    while pending:
        current, depth, app_value = pending.pop()
        if depth > _MAX_CLIENT_STRUCTURE_DEPTH:
            raise HTTPException(status_code=422, detail="Request structure is too deeply nested.")
        if isinstance(current, Mapping) and "user_id" in current:
            raise HTTPException(status_code=422, detail="user_id is assigned from the authenticated account.")
        if app_value:
            if current is None:
                continue
            if isinstance(current, str):
                try:
                    validate_app_id(current)
                except ValueError as exc:
                    raise HTTPException(status_code=422, detail=str(exc)) from exc
                continue
            if isinstance(current, Mapping):
                pending.extend((nested, depth + 1, True) for nested in current.values())
                continue
            if isinstance(current, Sequence) and not isinstance(current, (str, bytes)):
                pending.extend((nested, depth + 1, True) for nested in current)
                continue
            raise HTTPException(status_code=422, detail="app_id contains an invalid project identifier.")
        if isinstance(current, Mapping):
            for key, nested in current.items():
                pending.append((nested, depth + 1, key == "app_id"))
        elif isinstance(current, Sequence) and not isinstance(current, (str, bytes)):
            pending.extend((nested, depth + 1, False) for nested in current)


def owner_filters(
    principal: MemoryPrincipal,
    *,
    agent_id: str | None = None,
    run_id: str | None = None,
    app_id: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    """Combine optional filters with the authenticated account owner and project scope."""
    client_filters = dict(extra) if extra is not None else {}
    _validate_structured_filters(client_filters)

    trusted_filters: dict[str, object] = {}
    if agent_id is not None:
        trusted_filters["agent_id"] = agent_id
    if run_id is not None:
        trusted_filters["run_id"] = run_id
    if app_id is not None:
        trusted_filters["app_id"] = validate_app_id(app_id)

    if client_filters:
        clauses: list[dict[str, object]] = [{"user_id": principal.owner_id}]
        if trusted_filters:
            clauses.append(trusted_filters)
        clauses.append(client_filters)
        return {"AND": clauses}
    return {"user_id": principal.owner_id, **trusted_filters}


def require_owned_memory(memory_id: str, principal: MemoryPrincipal, memory: Any) -> OutputData:
    """Fetch a vector row and make missing and foreign IDs indistinguishable."""
    row = memory.vector_store.get(memory_id)
    payload = getattr(row, "payload", None)
    if not isinstance(payload, Mapping) or payload.get("user_id") != principal.owner_id:
        raise HTTPException(status_code=404, detail="Memory not found.")
    return row
