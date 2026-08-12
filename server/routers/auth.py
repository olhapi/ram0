import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from memory_owner_migration import migrate_legacy_ownership
from pydantic import BaseModel, EmailStr
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from auth import (
    consume_refresh_jti,
    create_access_token,
    create_refresh_token,
    decode_token,
    dummy_verify_password,
    hash_password,
    require_auth,
    verify_password,
)
from category_runtime import initialize_category_runtime
from db import get_db
from email_locks import lock_normalized_email
from invitations import hash_invitation_token, invitation_is_expired
from memory_owner_migration import require_ownership_ready
from models import User, UserInvitation
from rate_limit import limiter
from schemas import MessageResponse
from telemetry import capture_admin_registered, capture_onboarding_completed

router = APIRouter(prefix="/auth", tags=["auth"])

MIN_PASSWORD_LENGTH = 8
_BOOTSTRAP_REGISTRATION_LOCK_KEY = "ram0-bootstrap-registration"


def _require_password_length(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Password must be at least {MIN_PASSWORD_LENGTH} characters.",
        )


def _lock_bootstrap_registration(db: Session) -> None:
    """Serialize the zero-account decision through the administrator commit."""
    if db.bind is None or db.bind.dialect.name != "postgresql":
        return
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
        {"lock_key": _BOOTSTRAP_REGISTRATION_LOCK_KEY},
    )


class RegisterRequest(BaseModel):
    name: str
    email: EmailStr
    password: str


class OnboardingCompleteRequest(BaseModel):
    use_case: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class UpdateProfileRequest(BaseModel):
    name: str | None = None
    email: EmailStr | None = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class InvitationAcceptRequest(BaseModel):
    model_config = {"extra": "forbid"}

    token: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: uuid.UUID
    name: str
    email: str
    role: str
    created_at: datetime

    model_config = {"from_attributes": True}


class SetupStatusResponse(BaseModel):
    needsSetup: bool


@router.get("/setup-status", response_model=SetupStatusResponse)
def setup_status(db: Session = Depends(get_db)):
    count = db.scalar(select(func.count(User.id)))
    return SetupStatusResponse(needsSetup=count == 0)


@router.post("/register", response_model=TokenResponse)
@limiter.limit("5/minute")
def register(request: Request, body: RegisterRequest, db: Session = Depends(get_db)):
    """Create the first admin account. Blocked once any user exists."""
    _require_password_length(body.password)

    # This global bootstrap fence is always acquired before inspecting users.
    # It is transaction-scoped, so the winner holds it through the user commit.
    _lock_bootstrap_registration(db)
    if db.scalar(select(func.count(User.id))) > 0:
        raise HTTPException(status_code=403, detail="Registration is closed. An admin account already exists.")

    user = User(
        name=body.name,
        email=body.email,
        password_hash=hash_password(body.password),
        role="admin",
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=403, detail="Registration is closed. An admin account already exists.")
    db.refresh(user)

    capture_admin_registered(email=body.email)

    migration = migrate_legacy_ownership()
    if migration.state != "ready":
        raise HTTPException(
            status_code=503,
            detail="Memory ownership migration is in maintenance. Please try again later.",
        )
    initialize_category_runtime()

    return TokenResponse(
        access_token=create_access_token(str(user.id), user.role),
        refresh_token=create_refresh_token(str(user.id), db),
    )


@router.post("/login", response_model=TokenResponse)
@limiter.limit("10/minute")
def login(request: Request, body: LoginRequest, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email))
    if user is None:
        dummy_verify_password()
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    if user.disabled_at is not None:
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    user.last_login_at = datetime.now(timezone.utc)
    db.commit()

    return TokenResponse(
        access_token=create_access_token(str(user.id), user.role),
        refresh_token=create_refresh_token(str(user.id), db),
    )


@router.post("/refresh", response_model=TokenResponse)
@limiter.limit("20/minute")
def refresh(request: Request, body: RefreshRequest, db: Session = Depends(get_db)):
    payload = decode_token(body.refresh_token)
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid token type.")

    jti = payload.get("jti")
    if not jti:
        raise HTTPException(status_code=401, detail="Refresh token is no longer valid.")

    user = db.get(User, payload["sub"])
    if user is None or user.disabled_at is not None:
        raise HTTPException(status_code=401, detail="Refresh token is no longer valid.")

    consume_refresh_jti(jti, db)

    return TokenResponse(
        access_token=create_access_token(str(user.id), user.role),
        refresh_token=create_refresh_token(str(user.id), db),
    )


@router.post("/invitations/accept", response_model=TokenResponse)
@limiter.limit("10/minute")
def accept_invitation(request: Request, body: InvitationAcceptRequest, db: Session = Depends(get_db)):
    """Atomically turn one valid copied-link invitation into a member account."""
    require_ownership_ready()
    _require_password_length(body.password)

    now = datetime.now(timezone.utc)
    invitation = db.scalar(
        select(UserInvitation).where(UserInvitation.token_hash == hash_invitation_token(body.token)).with_for_update()
    )
    if (
        invitation is None
        or invitation.accepted_at is not None
        or invitation.revoked_at is not None
        or invitation_is_expired(invitation.expires_at, now)
    ):
        raise HTTPException(status_code=400, detail="Invitation is invalid or expired.")

    lock_normalized_email(db, invitation.email.lower())
    if db.scalar(select(User.id).where(func.lower(User.email) == invitation.email.lower())) is not None:
        raise HTTPException(status_code=400, detail="Invitation is invalid or expired.")

    user = User(
        name=invitation.email,
        email=invitation.email,
        password_hash=hash_password(body.password),
        role="member",
    )
    invitation.accepted_at = now
    db.add(user)
    try:
        db.flush()
        refresh_token = create_refresh_token(str(user.id), db, commit=False)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Invitation is invalid or expired.")

    return TokenResponse(
        access_token=create_access_token(str(user.id), user.role),
        refresh_token=refresh_token,
    )


@router.get("/me", response_model=UserResponse)
def me(user: User = Depends(require_auth)):
    return user


@router.patch("/me", response_model=UserResponse)
def update_me(
    body: UpdateProfileRequest,
    user: User = Depends(require_auth),
    db: Session = Depends(get_db),
):
    # require_auth resolves the user in its own short-lived session, so `user` is
    # detached from this request's `db`. Load a session-managed copy to mutate.
    db_user = db.get(User, user.id)
    if db_user is None:
        raise HTTPException(status_code=404, detail="User not found.")

    if body.name is not None and body.name.strip():
        db_user.name = body.name.strip()

    if body.email is not None and body.email != db_user.email:
        normalized_email = str(body.email).strip().lower()
        lock_normalized_email(db, normalized_email)
        collision = db.scalar(select(User).where(func.lower(User.email) == normalized_email, User.id != db_user.id))
        pending_invitation = db.scalar(
            select(UserInvitation).where(
                func.lower(UserInvitation.email) == normalized_email,
                UserInvitation.accepted_at.is_(None),
                UserInvitation.revoked_at.is_(None),
            )
        )
        if collision is not None or pending_invitation is not None:
            raise HTTPException(status_code=409, detail="Email is already in use.")
        db_user.email = normalized_email

    db.commit()
    return db_user


@router.post("/change-password", response_model=MessageResponse)
def change_password(
    body: ChangePasswordRequest,
    user: User = Depends(require_auth),
    db: Session = Depends(get_db),
):
    # require_auth resolves the user in its own short-lived session, so `user` is
    # detached from this request's `db`. Load a session-managed copy to mutate.
    db_user = db.get(User, user.id)
    if db_user is None or not verify_password(body.current_password, db_user.password_hash):
        raise HTTPException(status_code=401, detail="Current password is incorrect.")

    _require_password_length(body.new_password)

    db_user.password_hash = hash_password(body.new_password)
    db.commit()
    return MessageResponse(message="Password updated.")


@router.post("/onboarding-complete", response_model=MessageResponse)
def onboarding_complete(body: OnboardingCompleteRequest, user: User = Depends(require_auth)):
    """Fire the one-shot telemetry event after the setup wizard reaches its success state."""
    capture_onboarding_completed(email=user.email, use_case=body.use_case)
    return MessageResponse(message="Onboarding completed.")
