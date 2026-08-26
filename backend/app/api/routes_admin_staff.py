from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session, selectinload

from app.core.deps import require_permission
from app.core.security import hash_password
from app.crud.audit import write_audit_event
from app.crud.session import revoke_all_sessions
from app.crud.user import generate_staff_username
from app.database import get_db
from app.models.audit_log import AuthAuditLog, AuthEventType
from app.models.batch import Batch
from app.models.chat import Conversation, DirectMessage
from app.models.session import AuthSession
from app.models.staff import StaffProfile
from app.models.user import RoleEnum, User
from app.schemas.admin_staff import CreateStaffRequest, StaffAdminOut, UpdateStaffRequest, _clean_permissions

router = APIRouter(prefix="/admin", tags=["admin-staff"])


def _client_meta(request: Request) -> tuple[str, str]:
    ip = request.client.host if request.client else "unknown"
    ua = request.headers.get("user-agent", "unknown")
    return ip, ua


def _get_staff_or_404(db: Session, staff_id: int) -> User:
    user = db.get(User, staff_id)
    if user is None or user.role != RoleEnum.staff:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trainer not found")
    return user


def _staff_out(user: User) -> StaffAdminOut:
    profile = user.staff_profile
    return StaffAdminOut(
        id=user.id, username=user.username, full_name=user.full_name, photo_url=user.photo_url,
        email=profile.email if profile else None,
        is_active=user.is_active, must_change_password=user.must_change_password,
        last_login_at=user.last_login_at, created_at=user.created_at,
        permissions=(profile.permissions or {}) if profile else {},
    )


@router.post("/staff", response_model=StaffAdminOut, status_code=201)
def create_staff(
    payload: CreateStaffRequest,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("trainers.create")),
):
    username = generate_staff_username(db, payload.full_name)

    user = User(
        username=username,
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

    ip, ua = _client_meta(request)
    write_audit_event(
        db, AuthEventType.trainer_created, ip, ua, user=actor, detail=f"Created trainer {username}",
        module="trainers", target=username, status="success",
    )

    return _staff_out(user)


@router.get("/staff", response_model=list[StaffAdminOut])
def list_staff(
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("trainers.view")),
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
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("trainers.edit")),
):
    user = _get_staff_or_404(db, staff_id)
    profile = user.staff_profile
    ip, ua = _client_meta(request)

    if payload.full_name is not None:
        user.full_name = payload.full_name
    if payload.email is not None:
        profile.email = payload.email or None
    if payload.permissions is not None:
        profile.permissions = _clean_permissions(payload.permissions)
        write_audit_event(
            db, AuthEventType.trainer_permissions_changed, ip, ua, user=actor,
            detail=f"Changed permissions for trainer {user.username}", module="trainers", target=user.username,
            status="success",
        )

    if payload.is_active is not None:
        if payload.is_active != user.is_active:
            write_audit_event(
                db, AuthEventType.trainer_enabled if payload.is_active else AuthEventType.trainer_disabled,
                ip, ua, user=actor, detail=f"{'Enabled' if payload.is_active else 'Disabled'} trainer {user.username}",
                module="trainers", target=user.username, status="success",
            )
        user.is_active = payload.is_active
        if payload.is_active is False:
            revoke_all_sessions(db, user.id)

    if payload.reset_temp_password is not None:
        user.password_hash = hash_password(payload.reset_temp_password)
        user.must_change_password = True
        user.failed_login_count = 0
        user.locked_until = None
        revoke_all_sessions(db, user.id)
        write_audit_event(
            db, AuthEventType.trainer_password_reset, ip, ua, user=actor,
            detail=f"Reset password for trainer {user.username}", module="trainers", target=user.username,
            status="success",
        )

    if payload.full_name is not None or payload.email is not None:
        write_audit_event(
            db, AuthEventType.trainer_updated, ip, ua, user=actor, detail=f"Edited trainer {user.username}",
            module="trainers", target=user.username, status="success",
        )

    db.add(user)
    db.add(profile)
    db.commit()
    db.refresh(user)

    return _staff_out(user)


@router.delete("/staff/{staff_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_staff(
    staff_id: int,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("trainers.delete")),
):
    user = _get_staff_or_404(db, staff_id)
    username = user.username

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

    # A batch survives its trainer being deleted — unassign rather than
    # cascade, since batches.trainer_id has no ON DELETE CASCADE and the
    # batch (with its students) is meant to outlive any one trainer.
    db.execute(update(Batch).where(Batch.trainer_id == user.id).values(trainer_id=None))

    db.delete(user)  # cascades to staff_profile via the User.staff_profile relationship
    db.commit()

    ip, ua = _client_meta(request)
    write_audit_event(
        db, AuthEventType.trainer_deleted, ip, ua, user=actor, detail=f"Deleted trainer {username}",
        module="trainers", target=username, status="success",
    )
    return None
