from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session, selectinload

from app.core.deps import require_role
from app.core.security import hash_password
from app.crud.user import get_by_username
from app.database import get_db
from app.models.audit_log import AuthAuditLog
from app.models.chat import Conversation, DirectMessage
from app.models.session import AuthSession
from app.models.staff import StaffProfile
from app.models.user import RoleEnum, User
from app.schemas.admin_staff import CreateStaffRequest, StaffAdminOut, UpdateStaffRequest, _clean_permissions

router = APIRouter(prefix="/admin", tags=["admin-staff"])


def _get_staff_or_404(db: Session, staff_id: int) -> User:
    user = db.get(User, staff_id)
    if user is None or user.role != RoleEnum.staff:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trainer not found")
    return user


def _staff_out(user: User) -> StaffAdminOut:
    profile = user.staff_profile
    return StaffAdminOut(
        id=user.id, username=user.username, full_name=user.full_name,
        email=profile.email if profile else None,
        is_active=user.is_active, must_change_password=user.must_change_password,
        last_login_at=user.last_login_at, created_at=user.created_at,
        permissions=(profile.permissions or {}) if profile else {},
    )


def _revoke_active_sessions(db: Session, user_id: int) -> None:
    db.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(timezone.utc))
    )


@router.post("/staff", response_model=StaffAdminOut, status_code=201)
def create_staff(
    payload: CreateStaffRequest,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_role("admin")),
):
    if get_by_username(db, payload.username) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="That username is already taken.")

    user = User(
        username=payload.username,
        password_hash=hash_password(payload.temp_password),
        full_name=payload.full_name,
        role=RoleEnum.staff,
        must_change_password=True,
    )
    db.add(user)
    db.flush()

    db.add(StaffProfile(user_id=user.id, email=payload.email))
    db.commit()
    db.refresh(user)

    return _staff_out(user)


@router.get("/staff", response_model=list[StaffAdminOut])
def list_staff(
    db: Session = Depends(get_db),
    _admin: User = Depends(require_role("admin")),
):
    users = db.scalars(
        select(User)
        .where(User.role == RoleEnum.staff)
        .options(selectinload(User.staff_profile))
        .order_by(User.created_at.desc())
    ).all()
    return [_staff_out(u) for u in users]


@router.patch("/staff/{staff_id}", response_model=StaffAdminOut)
def update_staff(
    staff_id: int,
    payload: UpdateStaffRequest,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_role("admin")),
):
    user = _get_staff_or_404(db, staff_id)
    profile = user.staff_profile

    if payload.full_name is not None:
        user.full_name = payload.full_name
    if payload.email is not None:
        profile.email = payload.email or None
    if payload.permissions is not None:
        profile.permissions = _clean_permissions(payload.permissions)

    if payload.is_active is not None:
        user.is_active = payload.is_active
        if payload.is_active is False:
            _revoke_active_sessions(db, user.id)

    if payload.reset_temp_password is not None:
        user.password_hash = hash_password(payload.reset_temp_password)
        user.must_change_password = True
        user.failed_login_count = 0
        user.locked_until = None
        _revoke_active_sessions(db, user.id)

    db.add(user)
    db.add(profile)
    db.commit()
    db.refresh(user)

    return _staff_out(user)


@router.delete("/staff/{staff_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_staff(
    staff_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_role("admin")),
):
    user = _get_staff_or_404(db, staff_id)

    # Detach (don't delete) audit history so security records survive account
    # removal — auth_audit_log.user_id is nullable for exactly this reason.
    db.execute(update(AuthAuditLog).where(AuthAuditLog.user_id == user.id).values(user_id=None))
    db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))

    # Chat rows have no ON DELETE CASCADE to users.id, so any conversation this
    # account took part in (as either side) must be cleared first or the
    # user delete below fails on a foreign key violation.
    convo_ids = db.scalars(
        select(Conversation.id).where(or_(Conversation.user_a_id == user.id, Conversation.user_b_id == user.id))
    ).all()
    if convo_ids:
        db.execute(delete(DirectMessage).where(DirectMessage.conversation_id.in_(convo_ids)))
        db.execute(delete(Conversation).where(Conversation.id.in_(convo_ids)))

    db.delete(user)  # cascades to staff_profile via the User.staff_profile relationship
    db.commit()
    return None
