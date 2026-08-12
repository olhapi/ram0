"""Invitation and member-account lifecycle contracts."""

import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, event, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from auth import _BOOTSTRAP_ADMIN, create_access_token, require_admin, verify_auth
from dashboard_url import dashboard_origin, validate_dashboard_origin
from db import Base, get_db
from models import APIKey, RefreshTokenJti, User, UserInvitation
from memory_owner_migration import OwnershipMigrationResult
from routers import auth as auth_router
from routers import users as users_router
from rate_limit import limiter


class _SqliteSession(Session):
    """Match PostgreSQL UUID coercion used by the production session."""

    def get(self, entity, ident, **kwargs):
        if entity in {User, UserInvitation} and isinstance(ident, str):
            ident = uuid.UUID(ident)
        return super().get(entity, ident, **kwargs)


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[User.__table__, UserInvitation.__table__, APIKey.__table__, RefreshTokenJti.__table__],
    )
    session = sessionmaker(bind=engine, class_=_SqliteSession, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def admin(db_session: Session) -> User:
    account = User(name="Admin", email="admin@example.com", password_hash="admin-hash", role="admin")
    db_session.add(account)
    db_session.commit()
    return account


@pytest.fixture
def member(db_session: Session) -> User:
    account = User(name="Member", email="member@example.com", password_hash="member-hash", role="member")
    db_session.add(account)
    db_session.commit()
    return account


@pytest.fixture
def app(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    limiter.reset()
    api = FastAPI()
    api.include_router(auth_router.router)
    api.include_router(users_router.router)
    api.dependency_overrides[get_db] = lambda: db_session
    original_jwt_secret = create_access_token.__globals__["JWT_SECRET"]
    create_access_token.__globals__["JWT_SECRET"] = "invitation-test-secret"
    monkeypatch.setattr("routers.auth.hash_password", lambda password: f"hashed:{password}")
    monkeypatch.setattr(users_router, "require_ownership_ready", lambda: None)
    monkeypatch.setattr(auth_router, "require_ownership_ready", lambda: None)
    monkeypatch.setenv("DASHBOARD_URL", "https://ram0.example.lan")
    yield api
    limiter.reset()
    create_access_token.__globals__["JWT_SECRET"] = original_jwt_secret


@pytest.fixture
def admin_client(app: FastAPI, admin: User) -> TestClient:
    app.dependency_overrides[verify_auth] = lambda: admin
    return TestClient(app)


def _create_invitation(client: TestClient, email: str = "invited@example.com") -> tuple[str, str]:
    response = client.post("/admin/invitations", json={"email": email})
    assert response.status_code == 201, response.text
    invite_url = response.json()["invite_url"]
    return response.json()["id"], invite_url.removeprefix("https://ram0.example.lan/invite#token=")


def _generic_accept_failure(client: TestClient, token: str) -> None:
    response = client.post("/auth/invitations/accept", json={"token": token, "password": "correct horse"})
    assert (response.status_code, response.json()) == (400, {"detail": "Invitation is invalid or expired."})


def test_create_invitation_returns_raw_url_once_and_never_serializes_secrets(admin_client, db_session):
    response = admin_client.post("/admin/invitations", json={"email": "Member@Example.com"})

    assert response.status_code == 201
    payload = response.json()
    assert payload["email"] == "member@example.com"
    assert payload["invite_url"].startswith("https://ram0.example.lan/invite#token=")
    raw_token = payload["invite_url"].split("#token=", 1)[1]
    saved = db_session.scalar(select(UserInvitation).where(UserInvitation.id == uuid.UUID(payload["id"])))
    assert saved is not None
    assert len(saved.token_hash) == 64
    assert saved.token_hash == users_router.hash_invitation_token(raw_token)
    assert raw_token not in saved.token_hash

    listed = admin_client.get("/admin/users")
    assert listed.status_code == 200
    assert raw_token not in listed.text
    assert "token_hash" not in listed.text
    assert "password_hash" not in listed.text


@pytest.mark.parametrize(
    "configured_url",
    [
        "ftp://ram0.example.lan",
        "https://user:password@ram0.example.lan",
        "https://ram0.example.lan/invite",
        "https://ram0.example.lan?next=https://attacker.example",
        "https://ram0.example.lan/#fragment",
        "https:///missing-host",
        "https://ram0.example.lan\\wrong-path",
        "https://ram0.example.lan:",
        "https://ram0.example.lan:99999",
        "https://ram0.example.lan\x00",
        "https://ram0.example.lan\r\n.evil.example",
        "HTTPS://RAM0.EXAMPLE.LAN",
        "http://2130706433",
        "http://127.1",
        "http://0x7f000001",
        "http://0177.0.0.1",
        "http://127.0.0.01",
        "http://ram0.example.lan:80",
        "https://ram0.example.lan:443",
    ],
)
def test_dashboard_origin_rejects_non_origin_or_unsafe_urls(configured_url):
    with pytest.raises(ValueError, match="DASHBOARD_URL"):
        validate_dashboard_origin(configured_url)


@pytest.mark.parametrize(
    "configured_url",
    [
        "https://xn--a",
        "https://xn--hello-world.example.lan",
        "https://api.xn--abc.example.lan",
        "https://xn--a.xn--abc.example.lan:8443",
        "https://XN--ABC.example.lan",
    ],
)
def test_dashboard_origin_rejects_ace_labels(configured_url):
    with pytest.raises(ValueError, match="DASHBOARD_URL"):
        validate_dashboard_origin(configured_url)


@pytest.mark.parametrize(
    ("configured_url", "expected"),
    [
        ("https://ram0.example.lan", "https://ram0.example.lan"),
        ("http://127.0.0.1", "http://127.0.0.1"),
        ("http://[::1]", "http://[::1]"),
        ("https://ram0.example.lan:8443", "https://ram0.example.lan:8443"),
    ],
)
def test_dashboard_origin_preserves_browser_canonical_origins(configured_url, expected):
    assert validate_dashboard_origin(configured_url) == expected


def test_dashboard_origin_normalizes_trailing_slash(monkeypatch):
    monkeypatch.setenv("DASHBOARD_URL", "https://ram0.example.lan/")

    assert dashboard_origin() == "https://ram0.example.lan"


def test_bootstrap_admin_must_be_persisted_before_inviting(app, monkeypatch):
    app.dependency_overrides[require_admin] = lambda: _BOOTSTRAP_ADMIN

    def ownership_not_ready() -> None:
        raise HTTPException(status_code=503, detail="Memory ownership migration is in maintenance.")

    monkeypatch.setattr(users_router, "require_ownership_ready", ownership_not_ready)

    response = TestClient(app, raise_server_exceptions=False).post(
        "/admin/invitations", json={"email": "member@example.com"}
    )

    assert (response.status_code, response.json()) == (409, {"detail": "Administrator setup is required."})


def test_invitation_rejects_normalized_pending_email_and_existing_account(admin_client, member):
    first = admin_client.post("/admin/invitations", json={"email": "Pending@Example.com"})
    duplicate = admin_client.post("/admin/invitations", json={"email": "pending@example.com"})
    existing = admin_client.post("/admin/invitations", json={"email": member.email})

    assert first.status_code == 201
    assert (duplicate.status_code, duplicate.json()) == (409, {"detail": "Email is already in use."})
    assert (existing.status_code, existing.json()) == (409, {"detail": "Email is already in use."})


def test_profile_email_cannot_claim_a_pending_invitation(app, admin_client, db_session, admin):
    _create_invitation(admin_client, "reserved@example.com")
    app.dependency_overrides[verify_auth] = lambda: admin

    response = TestClient(app, raise_server_exceptions=False).patch("/auth/me", json={"email": "reserved@example.com"})

    assert (response.status_code, response.json()) == (409, {"detail": "Email is already in use."})
    assert db_session.get(User, admin.id).email == "admin@example.com"


def test_member_cannot_manage_users(app, member):
    app.dependency_overrides[verify_auth] = lambda: member

    response = TestClient(app, raise_server_exceptions=False).get("/admin/users")

    assert (response.status_code, response.json()) == (403, {"detail": "Admin role required."})


def test_create_and_accept_are_blocked_until_ownership_is_ready(app, admin, monkeypatch):
    def blocked() -> None:
        raise HTTPException(
            status_code=503, detail="Memory ownership migration is in maintenance. Please try again later."
        )

    app.dependency_overrides[verify_auth] = lambda: admin
    monkeypatch.setattr(users_router, "require_ownership_ready", blocked)
    create_response = TestClient(app, raise_server_exceptions=False).post(
        "/admin/invitations", json={"email": "blocked@example.com"}
    )
    assert create_response.status_code == 503

    monkeypatch.setattr(users_router, "require_ownership_ready", lambda: None)
    invitation_id, token = _create_invitation(TestClient(app, raise_server_exceptions=False))
    assert invitation_id
    monkeypatch.setattr(auth_router, "require_ownership_ready", blocked)
    accept_response = TestClient(app, raise_server_exceptions=False).post(
        "/auth/invitations/accept", json={"token": token, "password": "correct horse"}
    )
    assert accept_response.status_code == 503


@pytest.mark.parametrize("state", ["unknown", "expired", "revoked", "accepted"])
def test_invalid_invitation_states_share_one_public_error(app, admin_client, db_session, state):
    if state == "unknown":
        _generic_accept_failure(TestClient(app, raise_server_exceptions=False), "not-a-real-token")
        return

    invitation_id, token = _create_invitation(admin_client, f"{state}@example.com")
    invitation = db_session.get(UserInvitation, invitation_id)
    assert invitation is not None
    if state == "expired":
        invitation.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db_session.commit()
    elif state == "revoked":
        revoked = admin_client.delete(f"/admin/invitations/{invitation_id}")
        assert revoked.status_code == 200
    else:
        assert create_access_token.__globals__["JWT_SECRET"] == "invitation-test-secret"
        accepted = TestClient(app).post("/auth/invitations/accept", json={"token": token, "password": "correct horse"})
        assert accepted.status_code == 200, accepted.text

    _generic_accept_failure(TestClient(app, raise_server_exceptions=False), token)


def test_invitation_expires_after_seven_days(admin_client, db_session):
    invitation_id, token = _create_invitation(admin_client)
    invitation = db_session.get(UserInvitation, invitation_id)
    assert invitation is not None
    assert invitation.expires_at - invitation.created_at == timedelta(days=7)
    invitation.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()

    _generic_accept_failure(admin_client, token)


def test_acceptance_is_single_use_and_creates_a_member_with_email_as_name(app, admin_client, db_session):
    invitation_id, token = _create_invitation(admin_client, "New.Member@example.com")
    client = TestClient(app)

    first = client.post("/auth/invitations/accept", json={"token": token, "password": "correct horse"})
    second = client.post("/auth/invitations/accept", json={"token": token, "password": "correct horse"})

    assert first.status_code == 200, first.text
    assert set(first.json()) == {"access_token", "refresh_token", "token_type"}
    assert second.status_code == 400
    invitation = db_session.get(UserInvitation, invitation_id)
    account = db_session.scalar(select(User).where(User.email == "new.member@example.com"))
    assert invitation is not None and invitation.accepted_at is not None
    assert account is not None
    assert (account.name, account.role, account.password_hash) == (
        "new.member@example.com",
        "member",
        "hashed:correct horse",
    )


def test_acceptance_enforces_password_policy(admin_client):
    _, token = _create_invitation(admin_client)

    response = admin_client.post("/auth/invitations/accept", json={"token": token, "password": "short"})

    assert (response.status_code, response.json()) == (400, {"detail": "Password must be at least 8 characters."})


def test_revoke_and_member_lifecycle_actions(admin_client, member, db_session):
    invitation_id, token = _create_invitation(admin_client, "revoke@example.com")

    revoked = admin_client.delete(f"/admin/invitations/{invitation_id}")
    disabled = admin_client.post(f"/admin/users/{member.id}/disable")

    assert revoked.status_code == 200
    _generic_accept_failure(admin_client, token)
    assert disabled.status_code == 200
    assert db_session.get(User, member.id).disabled_at is not None
    restored = admin_client.post(f"/admin/users/{member.id}/restore")
    assert restored.status_code == 200
    assert db_session.get(User, member.id).disabled_at is None


def test_invalid_ids_are_not_found_and_admins_cannot_be_disabled(admin_client, admin, db_session):
    unknown = str(uuid.uuid4())

    assert admin_client.delete("/admin/invitations/not-a-uuid").status_code == 404
    assert admin_client.post("/admin/users/not-a-uuid/disable").status_code == 404
    assert admin_client.post(f"/admin/users/{unknown}/restore").status_code == 404
    response = admin_client.post(f"/admin/users/{admin.id}/disable")
    assert (response.status_code, response.json()) == (409, {"detail": "Administrator accounts cannot be disabled."})
    privileged = User(name="Operator", email="operator@example.com", password_hash="operator-hash", role="operator")
    db_session.add(privileged)
    db_session.commit()

    disabled = admin_client.post(f"/admin/users/{privileged.id}/disable")
    restored = admin_client.post(f"/admin/users/{privileged.id}/restore")

    assert (disabled.status_code, disabled.json()) == (409, {"detail": "Only member accounts can be disabled."})
    assert (restored.status_code, restored.json()) == (409, {"detail": "Only member accounts can be disabled."})
    assert db_session.get(User, privileged.id).disabled_at is None


_POSTGRES_TIMEOUT_SECONDS = 5.0
_POSTGRES_POLL_SECONDS = 0.02
_POSTGRES_CONNECT_TIMEOUT_SECONDS = 3
_POSTGRES_OPERATION_TIMEOUT_MS = 5000


def _postgres_engine_or_skip():
    database_url = os.environ.get("TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("TEST_POSTGRES_URL is not configured")
    pytest.importorskip("psycopg")
    if database_url.startswith("postgres://"):
        database_url = f"postgresql+psycopg://{database_url.removeprefix('postgres://')}"
    engine = create_engine(
        database_url,
        pool_timeout=_POSTGRES_CONNECT_TIMEOUT_SECONDS,
        connect_args={
            "connect_timeout": _POSTGRES_CONNECT_TIMEOUT_SECONDS,
            "options": (
                f"-c statement_timeout={_POSTGRES_OPERATION_TIMEOUT_MS} "
                f"-c lock_timeout={_POSTGRES_OPERATION_TIMEOUT_MS}"
            ),
        },
    )
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except OperationalError as error:
        engine.dispose()
        pytest.fail(f"TEST_POSTGRES_URL is configured but unavailable: {error}")
    return engine


class _InvitationRowLockProbe:
    def __init__(self, engine, leader_name: str, contender_name: str):
        self.engine = engine
        self.leader_name = leader_name
        self.contender_name = contender_name
        self.leader_locked = threading.Event()
        self.release_leader = threading.Event()
        self.contender_attempted = threading.Event()
        self.leader_backend_pid: int | None = None
        self.contender_backend_pid: int | None = None
        event.listen(engine, "after_cursor_execute", self._after_execute)
        event.listen(engine, "before_cursor_execute", self._before_execute)

    def close(self) -> None:
        event.remove(self.engine, "after_cursor_execute", self._after_execute)
        event.remove(self.engine, "before_cursor_execute", self._before_execute)

    @staticmethod
    def _is_invitation_row_lock(statement: str) -> bool:
        return "user_invitations" in statement and "FOR UPDATE" in statement

    def _after_execute(self, connection, _cursor, statement, _parameters, _context, _executemany) -> None:
        if threading.current_thread().name != self.leader_name or not self._is_invitation_row_lock(statement):
            return
        self.leader_backend_pid = connection.connection.driver_connection.info.backend_pid
        self.leader_locked.set()
        if not self.release_leader.wait(_POSTGRES_TIMEOUT_SECONDS):
            raise TimeoutError("leader invitation acceptance was not released")

    def _before_execute(self, connection, _cursor, statement, _parameters, _context, _executemany) -> None:
        if threading.current_thread().name != self.contender_name or not self._is_invitation_row_lock(statement):
            return
        self.contender_backend_pid = connection.connection.driver_connection.info.backend_pid
        self.contender_attempted.set()


class _AdvisoryLockProbe:
    def __init__(self, engine, leader_name: str, contender_name: str):
        self.engine = engine
        self.leader_name = leader_name
        self.contender_name = contender_name
        self.leader_locked = threading.Event()
        self.release_leader = threading.Event()
        self.contender_attempted = threading.Event()
        self.leader_backend_pid: int | None = None
        self.contender_backend_pid: int | None = None
        event.listen(engine, "after_cursor_execute", self._after_execute)
        event.listen(engine, "before_cursor_execute", self._before_execute)

    def close(self) -> None:
        event.remove(self.engine, "after_cursor_execute", self._after_execute)
        event.remove(self.engine, "before_cursor_execute", self._before_execute)

    @staticmethod
    def _is_email_lock(statement: str) -> bool:
        return "pg_advisory_xact_lock" in statement and "hashtextextended" in statement

    def _after_execute(self, connection, _cursor, statement, _parameters, _context, _executemany) -> None:
        if threading.current_thread().name != self.leader_name or not self._is_email_lock(statement):
            return
        self.leader_backend_pid = connection.connection.driver_connection.info.backend_pid
        self.leader_locked.set()
        if not self.release_leader.wait(_POSTGRES_TIMEOUT_SECONDS):
            raise TimeoutError("leader email reservation was not released")

    def _before_execute(self, connection, _cursor, statement, _parameters, _context, _executemany) -> None:
        if threading.current_thread().name != self.contender_name or not self._is_email_lock(statement):
            return
        self.contender_backend_pid = connection.connection.driver_connection.info.backend_pid
        self.contender_attempted.set()


class _BootstrapRegistrationLockProbe:
    def __init__(self, engine, leader_name: str, contender_name: str):
        self.engine = engine
        self.leader_name = leader_name
        self.contender_name = contender_name
        self.leader_locked = threading.Event()
        self.release_leader = threading.Event()
        self.contender_attempted = threading.Event()
        self.user_query_before_lock = False
        self.leader_backend_pid: int | None = None
        self.contender_backend_pid: int | None = None
        self._locked_threads: set[str] = set()
        event.listen(engine, "before_cursor_execute", self._before_execute)
        event.listen(engine, "after_cursor_execute", self._after_execute)

    def close(self) -> None:
        event.remove(self.engine, "before_cursor_execute", self._before_execute)
        event.remove(self.engine, "after_cursor_execute", self._after_execute)

    @staticmethod
    def _is_bootstrap_lock(statement: str, parameters: object) -> bool:
        return (
            "pg_advisory_xact_lock" in statement
            and isinstance(parameters, dict)
            and parameters.get("lock_key") == "ram0-bootstrap-registration"
        )

    def _before_execute(self, connection, _cursor, statement, _parameters, _context, _executemany) -> None:
        thread_name = threading.current_thread().name
        if thread_name not in {self.leader_name, self.contender_name}:
            return
        if "FROM users" in statement and thread_name not in self._locked_threads:
            self.user_query_before_lock = True
        if not self._is_bootstrap_lock(statement, _parameters):
            return
        if thread_name == self.contender_name:
            self.contender_backend_pid = connection.connection.driver_connection.info.backend_pid
            self.contender_attempted.set()

    def _after_execute(self, connection, _cursor, statement, _parameters, _context, _executemany) -> None:
        if not self._is_bootstrap_lock(statement, _parameters):
            return
        thread_name = threading.current_thread().name
        self._locked_threads.add(thread_name)
        if thread_name != self.leader_name:
            return
        self.leader_backend_pid = connection.connection.driver_connection.info.backend_pid
        self.leader_locked.set()
        if not self.release_leader.wait(_POSTGRES_TIMEOUT_SECONDS):
            raise TimeoutError("leader bootstrap registration was not released")


def _wait_for_invitation_row_wait(engine, backend_pid: int) -> None:
    deadline = time.monotonic() + _POSTGRES_TIMEOUT_SECONDS
    observed = None
    while time.monotonic() < deadline:
        with engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        """
                    SELECT activity.state, activity.wait_event_type,
                           EXISTS (
                               SELECT 1 FROM pg_locks lock
                               WHERE lock.pid = activity.pid
                                 AND lock.locktype = 'tuple'
                                 AND lock.relation = 'user_invitations'::regclass
                           ) AS holds_invitation_tuple_lock,
                           EXISTS (
                               SELECT 1 FROM pg_locks lock
                               WHERE lock.pid = activity.pid
                                 AND lock.locktype = 'transactionid'
                                 AND NOT lock.granted
                           ) AS waits_for_locked_invitation_transaction
                    FROM pg_stat_activity activity
                    WHERE activity.pid = :backend_pid
                    """
                    ),
                    {"backend_pid": backend_pid},
                )
                .mappings()
                .one_or_none()
            )
        if row is not None:
            observed = dict(row)
            if (
                observed["state"] == "active"
                and observed["wait_event_type"] == "Lock"
                and observed["holds_invitation_tuple_lock"] is True
                and observed["waits_for_locked_invitation_transaction"] is True
            ):
                return
        time.sleep(_POSTGRES_POLL_SECONDS)
    pytest.fail(f"invitation contender did not wait on the invitation row: {observed}")


def _wait_for_email_advisory_wait(engine, backend_pid: int) -> None:
    deadline = time.monotonic() + _POSTGRES_TIMEOUT_SECONDS
    observed = None
    while time.monotonic() < deadline:
        with engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        """
                    SELECT activity.state, activity.wait_event_type, activity.wait_event,
                           EXISTS (
                               SELECT 1 FROM pg_locks lock
                               WHERE lock.pid = activity.pid
                                 AND lock.locktype = 'advisory'
                                 AND NOT lock.granted
                           ) AS waits_for_email_advisory_lock
                    FROM pg_stat_activity activity
                    WHERE activity.pid = :backend_pid
                    """
                    ),
                    {"backend_pid": backend_pid},
                )
                .mappings()
                .one_or_none()
            )
        if row is not None:
            observed = dict(row)
            if (
                observed["state"] == "active"
                and observed["wait_event_type"] == "Lock"
                and observed["wait_event"] == "advisory"
                and observed["waits_for_email_advisory_lock"] is True
            ):
                return
        time.sleep(_POSTGRES_POLL_SECONDS)
    pytest.fail(f"email contender did not wait on the advisory lock: {observed}")


def _wait_for_bootstrap_advisory_wait(engine, backend_pid: int) -> None:
    deadline = time.monotonic() + _POSTGRES_TIMEOUT_SECONDS
    observed = None
    while time.monotonic() < deadline:
        with engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        """
                    SELECT activity.state, activity.wait_event_type, activity.wait_event,
                           EXISTS (
                               SELECT 1 FROM pg_locks lock
                               WHERE lock.pid = activity.pid
                                 AND lock.locktype = 'advisory'
                                 AND NOT lock.granted
                           ) AS waits_for_bootstrap_advisory_lock
                    FROM pg_stat_activity activity
                    WHERE activity.pid = :backend_pid
                    """
                    ),
                    {"backend_pid": backend_pid},
                )
                .mappings()
                .one_or_none()
            )
        if row is not None:
            observed = dict(row)
            if (
                observed["state"] == "active"
                and observed["wait_event_type"] == "Lock"
                and observed["wait_event"] == "advisory"
                and observed["waits_for_bootstrap_advisory_lock"] is True
            ):
                return
        time.sleep(_POSTGRES_POLL_SECONDS)
    pytest.fail(f"bootstrap contender did not wait on the advisory lock: {observed}")


def test_postgres_bootstrap_registration_serializes_distinct_emails(monkeypatch):
    """Two zero-user registrations use distinct connections, but exactly one becomes administrator."""
    engine = _postgres_engine_or_skip()
    Base.metadata.create_all(engine, tables=[User.__table__, RefreshTokenJti.__table__])
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    email_prefix = f"bootstrap-{uuid.uuid4()}"
    emails = [f"{email_prefix}-leader@example.com", f"{email_prefix}-contender@example.com"]
    outcomes: list[tuple[int, object]] = []
    errors: list[BaseException] = []
    monkeypatch.setattr(auth_router, "hash_password", lambda password: f"hashed:{password}")
    monkeypatch.setattr(
        auth_router,
        "migrate_legacy_ownership",
        lambda: OwnershipMigrationResult("ready", 0, 0),
    )
    monkeypatch.setattr(auth_router, "initialize_category_runtime", lambda: None)
    monkeypatch.setattr(auth_router, "capture_admin_registered", lambda **_kwargs: None)
    monkeypatch.setitem(create_access_token.__globals__, "JWT_SECRET", "bootstrap-race-test-secret")
    probe = _BootstrapRegistrationLockProbe(engine, "bootstrap-leader", "bootstrap-contender")

    def register_once(index: int) -> None:
        with session_factory() as session:
            try:
                auth_router.register.__wrapped__(
                    request=None,
                    body=auth_router.RegisterRequest(
                        name=f"Admin {index}",
                        email=emails[index],
                        password="correct horse",
                    ),
                    db=session,
                )
                outcomes.append((200, None))
            except HTTPException as error:
                outcomes.append((error.status_code, error.detail))
            except BaseException as error:
                errors.append(error)

    threads = [
        threading.Thread(target=register_once, args=(0,), name="bootstrap-leader", daemon=True),
        threading.Thread(target=register_once, args=(1,), name="bootstrap-contender", daemon=True),
    ]
    try:
        threads[0].start()
        assert probe.leader_locked.wait(_POSTGRES_TIMEOUT_SECONDS)
        threads[1].start()
        assert probe.contender_attempted.wait(_POSTGRES_TIMEOUT_SECONDS)
        assert probe.leader_backend_pid is not None
        assert probe.contender_backend_pid is not None
        assert probe.leader_backend_pid != probe.contender_backend_pid
        _wait_for_bootstrap_advisory_wait(engine, probe.contender_backend_pid)
    finally:
        probe.release_leader.set()
        for thread in threads:
            if thread.ident is not None:
                thread.join(_POSTGRES_TIMEOUT_SECONDS)
        probe.close()

    try:
        assert all(not thread.is_alive() for thread in threads)
        assert errors == []
        assert sorted(outcomes, key=lambda item: item[0]) == [
            (200, None),
            (403, "Registration is closed. An admin account already exists."),
        ]
        assert probe.user_query_before_lock is False
        with session_factory() as session:
            accounts = list(session.scalars(select(User).where(User.email.in_(emails))).all())
            assert len(accounts) == 1
            assert accounts[0].role == "admin"
    finally:
        with session_factory() as session:
            account_ids = list(session.scalars(select(User.id).where(User.email.in_(emails))).all())
            if account_ids:
                session.execute(delete(RefreshTokenJti).where(RefreshTokenJti.user_id.in_(account_ids)))
                session.execute(delete(User).where(User.id.in_(account_ids)))
                session.commit()
        engine.dispose()


def test_postgres_acceptance_locks_one_invitation_for_one_member(monkeypatch):
    """A second accept visibly waits on the invitation row before one transaction wins."""
    engine = _postgres_engine_or_skip()
    Base.metadata.create_all(engine, tables=[User.__table__, UserInvitation.__table__, RefreshTokenJti.__table__])
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    token = f"concurrency-test-{uuid.uuid4()}"
    admin = User(name="Admin", email=f"admin-{uuid.uuid4()}@example.com", password_hash="hash", role="admin")
    invitation_id = None
    email = None
    try:
        with session_factory() as session:
            session.add(admin)
            session.commit()
            invitation = UserInvitation(
                email=f"member-{uuid.uuid4()}@example.com",
                token_hash=users_router.hash_invitation_token(token),
                created_by=admin.id,
                role="member",
                expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            )
            session.add(invitation)
            session.commit()
            invitation_id = invitation.id
            email = invitation.email

        monkeypatch.setitem(create_access_token.__globals__, "JWT_SECRET", "invitation-test-secret")
        monkeypatch.setattr("routers.auth.hash_password", lambda password: f"hashed:{password}")
        monkeypatch.setattr(auth_router, "require_ownership_ready", lambda: None)
        statuses: list[int] = []
        errors: list[BaseException] = []
        probe = _InvitationRowLockProbe(engine, "invitation-leader", "invitation-contender")

        def accept_once() -> None:
            with session_factory() as session:
                try:
                    auth_router.accept_invitation.__wrapped__(
                        request=None,
                        body=auth_router.InvitationAcceptRequest(token=token, password="correct horse"),
                        db=session,
                    )
                    statuses.append(200)
                except HTTPException as error:
                    statuses.append(error.status_code)
                except BaseException as error:
                    errors.append(error)

        leader = threading.Thread(target=accept_once, name="invitation-leader", daemon=True)
        contender = threading.Thread(target=accept_once, name="invitation-contender", daemon=True)
        leader.start()
        try:
            assert probe.leader_locked.wait(_POSTGRES_TIMEOUT_SECONDS)
            contender.start()
            assert probe.contender_attempted.wait(_POSTGRES_TIMEOUT_SECONDS)
            assert probe.leader_backend_pid is not None
            assert probe.contender_backend_pid is not None
            assert probe.leader_backend_pid != probe.contender_backend_pid
            _wait_for_invitation_row_wait(engine, probe.contender_backend_pid)
        finally:
            probe.release_leader.set()
            leader.join(_POSTGRES_TIMEOUT_SECONDS)
            if contender.ident is not None:
                contender.join(_POSTGRES_TIMEOUT_SECONDS)
            probe.close()

        assert not leader.is_alive()
        assert not contender.is_alive()
        assert errors == []
        assert statuses.count(200) == 1
        assert statuses.count(400) == 1
        with session_factory() as session:
            accepted_member = session.scalar(select(User).where(User.email == email))
            assert accepted_member is not None
            assert session.get(UserInvitation, invitation_id).accepted_at is not None
    finally:
        with session_factory() as session:
            accepted_member_id = (
                session.scalar(select(User.id).where(User.email == email)) if email is not None else None
            )
            if accepted_member_id is not None:
                session.execute(delete(RefreshTokenJti).where(RefreshTokenJti.user_id == accepted_member_id))
                session.execute(delete(User).where(User.id == accepted_member_id))
            if invitation_id is not None:
                session.execute(delete(UserInvitation).where(UserInvitation.id == invitation_id))
            session.execute(delete(User).where(User.id == admin.id))
            session.commit()
        engine.dispose()


def test_postgres_invitation_creation_and_profile_email_share_one_fence(monkeypatch):
    """An invite and a profile update cannot both reserve the same normalized email."""
    engine = _postgres_engine_or_skip()
    Base.metadata.create_all(engine, tables=[User.__table__, UserInvitation.__table__, RefreshTokenJti.__table__])
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    email = f"shared-{uuid.uuid4()}@example.com"
    admin = User(name="Admin", email=f"admin-{uuid.uuid4()}@example.com", password_hash="hash", role="admin")
    profile = User(name="Profile", email=f"profile-{uuid.uuid4()}@example.com", password_hash="hash", role="member")
    monkeypatch.setattr(users_router, "require_ownership_ready", lambda: None)
    monkeypatch.setenv("DASHBOARD_URL", "https://ram0.example.lan")
    outcomes: list[int] = []
    errors: list[BaseException] = []
    try:
        with session_factory() as session:
            session.add_all([admin, profile])
            session.commit()

        probe = _AdvisoryLockProbe(engine, "email-lock-leader", "email-lock-contender")

        def create_invitation() -> None:
            with session_factory() as session:
                try:
                    users_router.create_invitation.__wrapped__(
                        request=None,
                        body=users_router.InvitationCreateRequest(email=email),
                        admin=admin,
                        db=session,
                    )
                    outcomes.append(201)
                except HTTPException as error:
                    outcomes.append(error.status_code)
                except BaseException as error:
                    errors.append(error)

        def update_profile() -> None:
            with session_factory() as session:
                try:
                    auth_router.update_me(
                        body=auth_router.UpdateProfileRequest(email=email),
                        user=profile,
                        db=session,
                    )
                    outcomes.append(200)
                except HTTPException as error:
                    outcomes.append(error.status_code)
                except BaseException as error:
                    errors.append(error)

        threads = [
            threading.Thread(target=create_invitation, name="email-lock-leader", daemon=True),
            threading.Thread(target=update_profile, name="email-lock-contender", daemon=True),
        ]
        threads[0].start()
        try:
            assert probe.leader_locked.wait(_POSTGRES_TIMEOUT_SECONDS)
            threads[1].start()
            assert probe.contender_attempted.wait(_POSTGRES_TIMEOUT_SECONDS)
            assert probe.leader_backend_pid is not None
            assert probe.contender_backend_pid is not None
            assert probe.leader_backend_pid != probe.contender_backend_pid
            _wait_for_email_advisory_wait(engine, probe.contender_backend_pid)
        finally:
            probe.release_leader.set()
            for thread in threads:
                if thread.ident is not None:
                    thread.join(_POSTGRES_TIMEOUT_SECONDS)
            probe.close()

        assert all(not thread.is_alive() for thread in threads)
        assert errors == []
        assert sorted(outcomes) in ([200, 409], [201, 409])
        with session_factory() as session:
            has_user = session.scalar(select(User).where(User.email == email)) is not None
            has_invitation = session.scalar(select(UserInvitation).where(UserInvitation.email == email)) is not None
            assert has_user ^ has_invitation
    finally:
        with session_factory() as session:
            session.execute(delete(RefreshTokenJti).where(RefreshTokenJti.user_id.in_([admin.id, profile.id])))
            session.execute(delete(UserInvitation).where(UserInvitation.created_by == admin.id))
            session.execute(delete(User).where(User.id.in_([admin.id, profile.id])))
            session.commit()
        engine.dispose()
