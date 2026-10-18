from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.deps import require_permission
from app.core.security import hash_password
from app.core.student_deletion import stage_student_deletion
from app.crud.audit import write_audit_event
from app.crud.session import revoke_all_sessions
from app.crud.user import generate_staff_code, generate_staff_username
from app.database import Base, get_db
from app.models.audit_log import AuthEventType
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
    try:
        db.add(user)
        db.flush()
        db.add(StaffProfile(user_id=user.id, email=payload.email, staff_code=generate_staff_code(db)))
        ip, ua = _client_meta(request)
        write_audit_event(
            db, AuthEventType.trainer_created, ip, ua, user=actor, detail=f"Created trainer {username}",
            module="trainers", target=username, status="success", commit=False,
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Trainer login or code already exists. Refresh and retry.")
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Unable to create trainer account. No changes were made.")
    db.refresh(user)

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
            status="success", commit=False,
        )

    if payload.is_active is not None:
        if payload.is_active != user.is_active:
            write_audit_event(
                db, AuthEventType.trainer_enabled if payload.is_active else AuthEventType.trainer_disabled,
                ip, ua, user=actor, detail=f"{'Enabled' if payload.is_active else 'Disabled'} trainer {user.username}",
                module="trainers", target=user.username, status="success", commit=False,
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
            status="success", commit=False,
        )

    if payload.full_name is not None or payload.email is not None:
        write_audit_event(
            db, AuthEventType.trainer_updated, ip, ua, user=actor, detail=f"Edited trainer {user.username}",
            module="trainers", target=user.username, status="success", commit=False,
        )

    db.add(user)
    db.add(profile)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Unable to update trainer. No changes were made.")
    db.refresh(user)

    return _staff_out(user)


@router.delete("/staff/{staff_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_staff(
    staff_id: int,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("trainers.delete")),
):
    try:
        user = db.scalar(select(User).where(User.id == staff_id).with_for_update())
        if user is None or user.role != RoleEnum.staff:
            raise HTTPException(status_code=404, detail="Trainer not found")

        # Keep shared conversations, batches, support and meeting history.
        # Accounts with related records can be deactivated through PATCH.
        exclusive_links = {
            ("staff_profiles", "user_id"), ("auth_sessions", "user_id"),
            ("auth_audit_log", "user_id"), ("user_google_identities", "user_id"),
            ("google_login_tickets", "user_id"),
        }
        for table in Base.metadata.sorted_tables:
            for column in table.c:
                if not any(fk.target_fullname == "users.id" for fk in column.foreign_keys):
                    continue
                if (table.name, column.name) in exclusive_links:
                    continue
                if db.scalar(select(column).where(column == staff_id).limit(1)) is not None:
                    raise HTTPException(status_code=409, detail="Trainer has related records. "
                                        "Deactivate the account to preserve shared history.")

        stage_student_deletion(db, user)  # Shared account cleanup helper; caller commits.
        ip, ua = _client_meta(request)
        write_audit_event(
            db, AuthEventType.trainer_deleted, ip, ua, user=actor,
            detail=f"Deleted trainer id={staff_id}; exclusive uploads queued for cleanup",
            module="trainers", target=f"trainer:{staff_id}", status="success", commit=False,
        )
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="A related record prevents trainer deletion. "
                            "No changes were made; deactivate the account instead.")
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Unable to delete trainer. No changes were made.")
    return None
