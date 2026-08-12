"""Bearer-only account authentication for the MCP transport."""

import asyncio
import uuid
from datetime import datetime, timezone

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")

from fastapi import HTTPException
from starlette.requests import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import auth
import mcp_auth
from db import Base
from models import APIKey, Settings, User


def request_with_headers(headers: dict[str, str]) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/mcp",
            "headers": [(name.lower().encode(), value.encode()) for name, value in headers.items()],
        }
    )


@pytest.fixture
def account_key(monkeypatch: pytest.MonkeyPatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[User.__table__, APIKey.__table__, Settings.__table__])
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    owner = User(
        id=uuid.UUID("00000000-0000-0000-0000-000000000111"),
        name="MCP owner",
        email="mcp-owner@example.com",
        password_hash="unused",
        role="member",
    )
    key = "m0sk_mcp_bearer_contract_key"
    with sessions() as session:
        session.add(owner)
        session.add(
            APIKey(
                key_prefix=key[:12],
                key_hash=auth.pwd_context.hash(key),
                label="MCP",
                created_by=owner.id,
            )
        )
        session.commit()

    monkeypatch.setattr(mcp_auth, "SessionLocal", sessions)
    monkeypatch.setattr(mcp_auth, "require_ownership_ready", lambda: None)
    try:
        yield owner, key, sessions
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": ""},
        {"Authorization": "Basic m0sk_mcp_bearer_contract_key"},
        {"Authorization": "Bearer"},
        {"Authorization": "Bearer "},
        {"Authorization": "Bearer  m0sk_mcp_bearer_contract_key"},
    ],
)
def test_mcp_rejects_missing_or_malformed_bearer_credentials(headers, account_key):
    """Removing the strict Bearer parser would admit malformed MCP credentials."""
    with pytest.raises(HTTPException) as error:
        asyncio.run(mcp_auth.require_mcp_bearer(request_with_headers(headers)))

    assert error.value.status_code == 401
    assert error.value.headers == {"WWW-Authenticate": "Bearer"}


def test_mcp_rejects_x_api_key_even_when_it_is_a_valid_account_key(account_key):
    """Falling back to REST's X-API-Key mode would violate the MCP transport contract."""
    _, key, _ = account_key

    with pytest.raises(HTTPException) as error:
        asyncio.run(mcp_auth.require_mcp_bearer(request_with_headers({"X-API-Key": key})))

    assert error.value.status_code == 401
    assert error.value.headers == {"WWW-Authenticate": "Bearer"}


def test_mcp_bearer_key_resolves_the_key_owners_immutable_principal(account_key):
    """Using a caller-supplied owner instead of the key owner crosses account boundaries."""
    owner, key, _ = account_key

    request = request_with_headers({"Authorization": f"Bearer {key}"})
    principal = asyncio.run(mcp_auth.require_mcp_bearer(request))

    assert principal == mcp_auth.MemoryPrincipal(owner_id=str(owner.id))
    assert request.state.auth_type == "mcp_bearer_api_key"


@pytest.mark.parametrize("state", ["revoked", "disabled_owner"])
def test_mcp_rejects_revoked_keys_and_disabled_key_owners(state, account_key):
    """Ignoring account/key lifecycle state would leave revoked MCP credentials usable."""
    owner, key, sessions = account_key
    with sessions() as session:
        api_key = session.query(APIKey).one()
        if state == "revoked":
            api_key.revoked_at = datetime.now(timezone.utc)
        else:
            stored_owner = session.get(User, owner.id)
            stored_owner.disabled_at = datetime.now(timezone.utc)
        session.commit()

    with pytest.raises(HTTPException) as error:
        asyncio.run(mcp_auth.require_mcp_bearer(request_with_headers({"Authorization": f"Bearer {key}"})))

    assert error.value.status_code == 401
    assert error.value.headers == {"WWW-Authenticate": "Bearer"}


def test_mcp_is_unavailable_until_the_ownership_migration_is_ready(account_key, monkeypatch):
    """Skipping the readiness gate would allow MCP to read pre-ownership memory data."""
    _, key, _ = account_key

    def migration_not_ready():
        raise HTTPException(status_code=503, detail="maintenance")

    monkeypatch.setattr(mcp_auth, "require_ownership_ready", migration_not_ready)

    with pytest.raises(HTTPException) as error:
        asyncio.run(mcp_auth.require_mcp_bearer(request_with_headers({"Authorization": f"Bearer {key}"})))

    assert (error.value.status_code, error.value.detail) == (503, "maintenance")
