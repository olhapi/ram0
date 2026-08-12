"""Administrative invitation and member-account lifecycle routes."""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from dashboard_url import dashboard_origin
from email_locks import lock_normalized_email
from fastapi import APIRouter, Depends, HTTPException, Request
from invitations import generate_invitation_token, hash_invitation_token, invitation_is_expired
from memory_owner_migration import require_ownership_ready
from pydantic import BaseModel, ConfigDict, EmailStr
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from auth import require_admin
from db import get_db
from models import User, UserInvitation
from rate_limit import limiter
from schemas import MessageResponse

router = APIRouter(prefix="/admin", tags=["users"])

INVITATION_LIFETIME = timedelta(days=7)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_email(email: EmailStr | str) -> str:
    return str(email).strip().lower()


class InvitationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr


class InvitationCreateResponse(BaseModel):
    id: uuid.UUID
    email: str
    expires_at: datetime
    invite_url: str


class AdminUserItem(BaseModel):
    id: uuid.UUID
    name: str
    email: str
    role: str
    created_at: datetime
    disabled_at: datetime | None


class PendingInvitationItem(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    created_at: datetime
    expires_at: datetime
    status: Literal["pending", "expired"]


class AdminUsersResponse(BaseModel):
    users: list[AdminUserItem]
    invitations: list[PendingInvitationItem]


def _not_found(detail: str) -> HTTPException:
    return HTTPException(status_code=404, detail=detail)


def _parse_uuid(value: str, detail: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (TypeError, ValueError):
        raise _not_found(detail)


def _email_is_unavailable(db: Session, email: str, *, excluded_user_id: uuid.UUID | None = None) -> bool:
    user_query = select(User.id).where(func.lower(User.email) == email)
    if excluded_user_id is not None:
        user_query = user_query.where(User.id != excluded_user_id)
    if db.scalar(user_query) is not None:
        return True
    return (
        db.scalar(
            select(UserInvitation.id).where(
                func.lower(UserInvitation.email) == email,
                UserInvitation.accepted_at.is_(None),
                UserInvitation.revoked_at.is_(None),
            )
        )
        is not None
    )


@router.post("/invitations", response_model=InvitationCreateResponse, status_code=201)
@limiter.limit("10/minute")
def create_invitation(
    request: Request,
    body: InvitationCreateRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    persisted_admin = db.get(User, admin.id)
    if persisted_admin is None or persisted_admin.role != "admin":
        raise HTTPException(status_code=409, detail="Administrator setup is required.")
    require_ownership_ready()
    origin = dashboard_origin()
    email = _normalize_email(body.email)
    lock_normalized_email(db, email)
    if _email_is_unavailable(db, email):
        raise HTTPException(status_code=409, detail="Email is already in use.")

    now = _utcnow()
    token = generate_invitation_token()
    invitation = UserInvitation(
        email=email,
        token_hash=hash_invitation_token(token),
        created_by=persisted_admin.id,
        role="member",
        created_at=now,
        expires_at=now + INVITATION_LIFETIME,
    )
    db.add(invitation)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Email is already in use.")
    db.refresh(invitation)

    return InvitationCreateResponse(
        id=invitation.id,
        email=invitation.email,
        expires_at=invitation.expires_at,
        invite_url=f"{origin}/invite#token={token}",
    )


@router.get("/users", response_model=AdminUsersResponse)
def list_users(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    del admin
    now = _utcnow()
    users = db.scalars(select(User).order_by(User.created_at.asc())).all()
    invitations = db.scalars(
        select(UserInvitation)
        .where(UserInvitation.accepted_at.is_(None), UserInvitation.revoked_at.is_(None))
        .order_by(UserInvitation.created_at.asc())
    ).all()
    return AdminUsersResponse(
        users=[
            AdminUserItem(
                id=user.id,
                name=user.name,
                email=user.email,
                role=user.role,
                created_at=user.created_at,
                disabled_at=user.disabled_at,
            )
            for user in users
        ],
        invitations=[
            PendingInvitationItem(
                id=invitation.id,
                email=invitation.email,
                role=invitation.role,
                created_at=invitation.created_at,
                expires_at=invitation.expires_at,
                status="expired" if invitation_is_expired(invitation.expires_at, now) else "pending",
            )
            for invitation in invitations
        ],
    )


@router.delete("/invitations/{invitation_id}", response_model=MessageResponse)
def revoke_invitation(
    invitation_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    del admin
    invitation = db.scalar(
        select(UserInvitation)
        .where(UserInvitation.id == _parse_uuid(invitation_id, "Invitation not found."))
        .with_for_update()
    )
    if invitation is None or invitation.accepted_at is not None or invitation.revoked_at is not None:
        raise _not_found("Invitation not found.")
    invitation.revoked_at = _utcnow()
    db.commit()
    return MessageResponse(message="Invitation revoked.")


def _member_or_error(db: Session, user_id: str) -> User:
    user = db.get(User, _parse_uuid(user_id, "User not found."))
    if user is None:
        raise _not_found("User not found.")
    if user.role == "admin":
        raise HTTPException(status_code=409, detail="Administrator accounts cannot be disabled.")
    if user.role != "member":
        raise HTTPException(status_code=409, detail="Only member accounts can be disabled.")
    return user


@router.post("/users/{user_id}/disable", response_model=MessageResponse)
def disable_user(user_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    del admin
    user = _member_or_error(db, user_id)
    if user.disabled_at is None:
        user.disabled_at = _utcnow()
        db.commit()
    return MessageResponse(message="User disabled.")


@router.post("/users/{user_id}/restore", response_model=MessageResponse)
def restore_user(user_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    del admin
    user = _member_or_error(db, user_id)
    if user.disabled_at is not None:
        user.disabled_at = None
        db.commit()
    return MessageResponse(message="User restored.")
