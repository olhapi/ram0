"""Account lifecycle authentication contracts."""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import auth
from auth import (
    _resolve_user_from_api_key,
    _resolve_user_from_jwt,
    create_access_token,
    create_refresh_token,
    require_admin,
    require_auth,
)
from db import Base, get_db
from models import APIKey, CategoryJob, RefreshTokenJti, User
from routers.auth import router


class _SqliteSession(Session):
    """Match PostgreSQL UUID coercion used by the production session."""

    def get(self, entity, ident, **kwargs):
        if entity is User and isinstance(ident, str):
            ident = uuid.UUID(ident)
        return super().get(entity, ident, **kwargs)


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[User.__table__, APIKey.__table__, RefreshTokenJti.__table__])
    session = sessionmaker(bind=engine, class_=_SqliteSession, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def user(db_session: Session) -> User:
    user = User(name="Account owner", email="owner@example.com", password_hash="unused")
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def access_token(user: User, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setitem(create_access_token.__globals__, "JWT_SECRET", "test-jwt-secret")
    return create_access_token(str(user.id), user.role)


@pytest.fixture
def refresh_token(db_session: Session, user: User, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setitem(create_refresh_token.__globals__, "JWT_SECRET", "test-jwt-secret")
    return create_refresh_token(str(user.id), db_session)


@pytest.fixture
def route_client(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db_session
    monkeypatch.setattr(
        "routers.auth.verify_password",
        lambda plain, hashed: plain == "correct-password" and hashed == "test-password-hash",
    )
    return TestClient(app)


@pytest.fixture
def api_key(db_session: Session, monkeypatch: pytest.MonkeyPatch, user: User) -> str:
    raw_key = "m0sk_account_auth_test"
    db_session.add(
        APIKey(
            key_prefix=raw_key[:12],
            key_hash="test-api-key-hash",
            label="test key",
            created_by=user.id,
        )
    )
    db_session.commit()
    monkeypatch.setitem(
        _resolve_user_from_api_key.__globals__,
        "verify_api_key_hash",
        lambda plain, hashed: plain == raw_key and hashed == "test-api-key-hash",
    )
    return raw_key


def test_disabled_jwt_owner_is_rejected(db_session, user, access_token):
    user.disabled_at = datetime.now(timezone.utc)
    db_session.commit()

    with pytest.raises(HTTPException) as error:
        _resolve_user_from_jwt(access_token, db_session)

    assert (error.value.status_code, error.value.detail) == (401, "Invalid or expired credentials.")


def test_disabled_api_key_owner_is_rejected(db_session, user, api_key):
    user.disabled_at = datetime.now(timezone.utc)
    db_session.commit()

    with pytest.raises(HTTPException) as error:
        _resolve_user_from_api_key(api_key, db_session)

    assert (error.value.status_code, error.value.detail) == (401, "Invalid or expired credentials.")


def test_invitation_model_never_has_a_raw_token():
    from models import UserInvitation

    assert "token" not in UserInvitation.__table__.columns
    assert "token_hash" in UserInvitation.__table__.columns
    assert "owner_id" in CategoryJob.__table__.columns


def test_disabled_login_returns_generic_unauthorized_response(route_client, db_session, user):
    user.password_hash = "test-password-hash"
    user.disabled_at = datetime.now(timezone.utc)
    db_session.commit()

    response = route_client.post(
        "/auth/login",
        json={"email": user.email, "password": "correct-password"},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid email or password."}


def test_disabled_refresh_returns_generic_unauthorized_response(route_client, db_session, user, refresh_token):
    user.disabled_at = datetime.now(timezone.utc)
    db_session.commit()

    response = route_client.post("/auth/refresh", json={"refresh_token": refresh_token})

    assert response.status_code == 401
    assert response.json() == {"detail": "Refresh token is no longer valid."}


@pytest.mark.asyncio
@pytest.mark.parametrize(("dependency", "auth_type"), [(require_auth, "admin_api_key"), (require_auth, "disabled")])
async def test_legacy_default_user_authentication_rejects_disabled_account(
    dependency, auth_type, db_session, user, monkeypatch
):
    user.disabled_at = datetime.now(timezone.utc)
    db_session.commit()
    monkeypatch.setattr(auth, "SessionLocal", lambda: db_session)
    request = SimpleNamespace(state=SimpleNamespace(auth_type=auth_type))

    with pytest.raises(HTTPException) as error:
        await dependency(request, None)

    assert (error.value.status_code, error.value.detail) == (401, "Invalid or expired credentials.")


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_type", ["admin_api_key", "disabled"])
async def test_legacy_default_admin_authentication_rejects_disabled_account(auth_type, db_session, user, monkeypatch):
    user.disabled_at = datetime.now(timezone.utc)
    db_session.commit()
    monkeypatch.setattr(auth, "SessionLocal", lambda: db_session)
    request = SimpleNamespace(state=SimpleNamespace(auth_type=auth_type))

    with pytest.raises(HTTPException) as error:
        await require_admin(request, None)

    assert (error.value.status_code, error.value.detail) == (401, "Invalid or expired credentials.")
