import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.deps import require_permission
from app.core.security import hash_password
from app.core.student_deletion import stage_student_deletion
from app.crud.audit import write_audit_event
from app.crud.permissions import is_super_admin
from app.crud.session import revoke_all_sessions
from app.crud.user import generate_admin_username, get_by_username
from app.database import Base, get_db
from app.models.admin_profile import AdminProfile
from app.models.admin_role import LAB_VM_ADMIN_SLUG, LEADS_ADMIN_SLUG, SUPER_ADMIN_SLUG, AdminRole
from app.models.audit_log import AuthEventType
from app.models.user import RoleEnum, User
from app.schemas.admin_users import AdminUserOut, CreateAdminUserRequest, UpdateAdminUserRequest

logger = logging.getLogger("admin_users")

router = APIRouter(prefix="/admin/admin-users", tags=["admin-users"])

# Roles only a Super Admin actor may assign — full-access Super Admin itself,
# plus Leads Database Admin and Lab VM Admin (an explicit ask for both:
# neither role should be self-service for a regular Admin to hand out).
RESTRICTED_ROLE_SLUGS = {SUPER_ADMIN_SLUG, LEADS_ADMIN_SLUG, LAB_VM_ADMIN_SLUG}


def _client_meta(request: Request) -> tuple[str, str]:
    ip = request.client.host if request.client else "unknown"
    ua = request.headers.get("user-agent", "unknown")
    return ip, ua


def _get_admin_or_404(db: Session, admin_id: int) -> User:
    user = db.get(User, admin_id)
    if user is None or user.role != RoleEnum.admin:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Admin user not found")
    return user


def _admin_out(user: User) -> AdminUserOut:
    return AdminUserOut(
        id=user.id, username=user.username, full_name=user.full_name,
        email=user.admin_profile.email if user.admin_profile else None,
        admin_role={"id": user.admin_role.id, "name": user.admin_role.name, "slug": user.admin_role.slug}
        if user.admin_role else None,
        is_active=user.is_active, must_change_password=user.must_change_password,
        last_login_at=user.last_login_at, created_at=user.created_at,
    )


def _count_active_super_admins(db: Session, exclude_user_id: int | None = None) -> int:
    query = (
        select(func.count())
        .select_from(User)
        .join(AdminRole, User.admin_role_id == AdminRole.id)
        .where(AdminRole.slug == SUPER_ADMIN_SLUG, User.is_active.is_(True))
    )
    if exclude_user_id is not None:
        query = query.where(User.id != exclude_user_id)
    return db.scalar(query) or 0


def _lock_super_admin_role(db: Session) -> None:
    """Serialize membership changes before evaluating the last-admin rule.

    Acquire this lock before the target user's row lock. PostgreSQL READ
    COMMITTED refreshes the subsequent count after a waiting transaction
    commits; reversing the lock order can deadlock two concurrent requests.
    """
    role_id = db.scalar(select(AdminRole.id).where(AdminRole.slug == SUPER_ADMIN_SLUG).with_for_update())
    if role_id is None:
        raise HTTPException(status_code=409, detail="Super Admin role is unavailable.")


def _get_role_or_400(db: Session, admin_role_id: int) -> AdminRole:
    role = db.get(AdminRole, admin_role_id)
    if role is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="That role does not exist.")
    return role


@router.get("", response_model=list[AdminUserOut])
def list_admin_users(
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("admin_users.view")),
):
    users = db.scalars(
        select(User)
        .where(User.role == RoleEnum.admin)
        .options(selectinload(User.admin_profile), selectinload(User.admin_role))
        .order_by(User.created_at.desc())
    ).all()
    return [_admin_out(u) for u in users]


@router.post("", response_model=AdminUserOut, status_code=201)
def create_admin_user(
    payload: CreateAdminUserRequest,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("admin_users.create")),
):
    role = _get_role_or_400(db, payload.admin_role_id)
    if role.slug in RESTRICTED_ROLE_SLUGS and (actor.admin_role is None or actor.admin_role.slug != SUPER_ADMIN_SLUG):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Only a Super Admin can create a {role.name} account.",
        )

    username = generate_admin_username(db, payload.full_name)
    user = User(
        username=username,
        password_hash=hash_password(payload.temp_password),
        full_name=payload.full_name,
        role=RoleEnum.admin,
        admin_role_id=role.id,
        must_change_password=True,
    )
    try:
        db.add(user)
        db.flush()
        db.add(AdminProfile(user_id=user.id))
        ip, ua = _client_meta(request)
        write_audit_event(
            db, AuthEventType.admin_user_created, ip, ua, user=actor,
            detail=f"Created admin {username} ({role.name})", module="admin_users", target=username, status="success",
            actor_role=(actor.admin_role.slug if actor.admin_role else actor.role.value), commit=False,
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Admin login already exists. Refresh and retry.")
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Unable to create admin account. No changes were made.")
    db.refresh(user)

    return _admin_out(user)


@router.patch("/{admin_id}", response_model=AdminUserOut)
def update_admin_user(
    admin_id: int,
    payload: UpdateAdminUserRequest,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("admin_users.edit")),
):
    _lock_super_admin_role(db)
    user = db.scalar(select(User).where(User.id == admin_id).with_for_update())
    if user is None or user.role != RoleEnum.admin:
        raise HTTPException(status_code=404, detail="Admin account not found")
    if is_super_admin(user) and not is_super_admin(actor):
        raise HTTPException(status_code=403, detail="Only a Super Admin can manage a Super Admin account.")
    profile = user.admin_profile
    ip, ua = _client_meta(request)
    actor_role_snapshot = actor.admin_role.slug if actor.admin_role else actor.role.value

    if payload.full_name is not None:
        user.full_name = payload.full_name
    if payload.email is not None:
        if profile is None:
            profile = AdminProfile(user_id=user.id)
            db.add(profile)
        profile.email = payload.email or None

    if payload.admin_role_id is not None and payload.admin_role_id != user.admin_role_id:
        new_role = _get_role_or_400(db, payload.admin_role_id)
        old_role = user.admin_role

        # Only a Super Admin actor may assign or remove a restricted role
        # (Super Admin itself, or Leads Database Admin).
        is_restricted_change = (
            (old_role is not None and old_role.slug in RESTRICTED_ROLE_SLUGS)
            or new_role.slug in RESTRICTED_ROLE_SLUGS
        )
        if is_restricted_change and (actor.admin_role is None or actor.admin_role.slug != SUPER_ADMIN_SLUG):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only a Super Admin can assign or remove this role.",
            )
        # Last-Super-Admin protection: block moving the last active Super
        # Admin to a different role.
        if old_role is not None and old_role.slug == SUPER_ADMIN_SLUG:
            if _count_active_super_admins(db, exclude_user_id=user.id) < 1:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot change the role of the last Super Admin. Promote another admin to Super Admin first.",
                )

        user.admin_role_id = new_role.id
        # commit=False: staged alongside is_active/password-reset/profile
        # changes below (if any of those branches also fire in this same
        # request) and the eventual db.commit() at the end of this function
        # — never committed here on its own. Same reasoning as the fix in
        # delete_student: a write_audit_event call that commits independently
        # would otherwise make this role change durable before this
        # function's own rollback-on-failure logic ever runs.
        write_audit_event(
            db, AuthEventType.admin_user_role_changed, ip, ua, user=actor,
            detail=f"Changed {user.username}'s role from {old_role.name if old_role else 'none'} to {new_role.name}",
            module="admin_users", target=user.username, status="success", actor_role=actor_role_snapshot,
            commit=False,
        )

    if payload.is_active is not None and payload.is_active != user.is_active:
        if user.id == actor.id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You cannot disable your own account.")
        if not payload.is_active and user.admin_role and user.admin_role.slug == SUPER_ADMIN_SLUG:
            if _count_active_super_admins(db, exclude_user_id=user.id) < 1:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot disable the last Super Admin.",
                )
        user.is_active = payload.is_active
        if not payload.is_active:
            revoke_all_sessions(db, user.id)
        # commit=False — see the role-change branch above for why: the
        # is_active flip and the session revocation must not be committed
        # independently of this function's own final commit/rollback.
        write_audit_event(
            db, AuthEventType.admin_user_enabled if payload.is_active else AuthEventType.admin_user_disabled,
            ip, ua, user=actor, detail=f"{'Enabled' if payload.is_active else 'Disabled'} admin {user.username}",
            module="admin_users", target=user.username, status="success", actor_role=actor_role_snapshot,
            commit=False,
        )

    if payload.reset_temp_password is not None:
        user.password_hash = hash_password(payload.reset_temp_password)
        user.must_change_password = True
        user.failed_login_count = 0
        user.locked_until = None
        revoke_all_sessions(db, user.id)
        # commit=False — same reasoning: the new password hash and the
        # session revocation must land atomically with everything else.
        write_audit_event(
            db, AuthEventType.admin_user_password_reset, ip, ua, user=actor,
            detail=f"Reset password for admin {user.username}", module="admin_users", target=user.username,
            status="success", actor_role=actor_role_snapshot, commit=False,
        )

    if payload.full_name is not None or payload.email is not None:
        # commit=False — same reasoning for the full_name/profile.email edit.
        write_audit_event(
            db, AuthEventType.admin_user_updated, ip, ua, user=actor, detail=f"Edited admin {user.username}",
            module="admin_users", target=user.username, status="success", actor_role=actor_role_snapshot,
            commit=False,
        )

    db.add(user)
    if profile is not None:
        db.add(profile)
    # Single commit for the whole request: user/profile field changes, any
    # session revocation, and every audit entry staged above (zero, one, or
    # several, depending on which payload fields were actually changed) all
    # land together or not at all. Previously each write_audit_event call
    # above committed independently and immediately — the exact bug fixed
    # in delete_student (app/api/routes_admin_students.py), present here
    # too: a failure at this point used to leave whichever branches had
    # already fired durably persisted, with no rollback ever possible for
    # them, while the request itself could still surface as a failure to
    # the caller.
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(user)

    return _admin_out(user)


@router.post("/{admin_id}/revoke-sessions", status_code=status.HTTP_204_NO_CONTENT)
def revoke_admin_sessions(
    admin_id: int,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("admin_users.edit")),
):
    user = _get_admin_or_404(db, admin_id)
    if is_super_admin(user) and not is_super_admin(actor):
        raise HTTPException(status_code=403, detail="Only a Super Admin can manage a Super Admin account.")
    try:
        revoke_all_sessions(db, user.id)
        ip, ua = _client_meta(request)
        write_audit_event(
            db, AuthEventType.session_revoked, ip, ua, user=actor,
            detail=f"Revoked all sessions for admin {user.username}",
            module="admin_users", target=user.username, status="success",
            actor_role=(actor.admin_role.slug if actor.admin_role else actor.role.value), commit=False,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Unable to revoke admin sessions. No changes were made.")
    return None


@router.delete("/{admin_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_admin_user(
    admin_id: int,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("admin_users.delete")),
):
    try:
        _lock_super_admin_role(db)
        user = db.scalar(select(User).where(User.id == admin_id).with_for_update())
        if user is None or user.role != RoleEnum.admin:
            raise HTTPException(status_code=404, detail="Admin account not found")
        if user.id == actor.id:
            raise HTTPException(status_code=400, detail="You cannot delete your own account.")
        if is_super_admin(user):
            if not is_super_admin(actor):
                raise HTTPException(status_code=403, detail="Only a Super Admin can delete a Super Admin account.")
            if _count_active_super_admins(db, exclude_user_id=user.id) < 1:
                raise HTTPException(status_code=400, detail="Cannot delete the last Super Admin.")

        # No admin deletion may erase another user's conversation or silently
        # detach a business record. Deactivate accounts with shared history.
        exclusive_links = {
            ("admin_profiles", "user_id"), ("auth_sessions", "user_id"),
            ("auth_audit_log", "user_id"), ("user_google_identities", "user_id"),
            ("google_login_tickets", "user_id"),
        }
        for table in Base.metadata.sorted_tables:
            for column in table.c:
                if not any(fk.target_fullname == "users.id" for fk in column.foreign_keys):
                    continue
                if (table.name, column.name) in exclusive_links:
                    continue
                if db.scalar(select(column).where(column == admin_id).limit(1)) is not None:
                    raise HTTPException(status_code=409, detail="Admin has related records. "
                                        "Deactivate the account to preserve shared history.")

        stage_student_deletion(db, user)  # Shared account cleanup helper; caller commits.
        ip, ua = _client_meta(request)
        write_audit_event(
            db, AuthEventType.admin_user_deleted, ip, ua, user=actor,
            detail=f"Deleted admin id={admin_id}; exclusive uploads queued for cleanup",
            module="admin_users", target=f"admin:{admin_id}", status="success",
            actor_role=(actor.admin_role.slug if actor.admin_role else actor.role.value), commit=False,
        )
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="A related record prevents admin deletion. "
                            "No changes were made; deactivate the account instead.")
    except Exception:
        db.rollback()
        logger.exception("Failed to delete admin user id=%s", admin_id)
        raise HTTPException(status_code=500, detail="Unable to delete admin account. No changes were made.")
    return None
