from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session, selectinload

from app.core.deps import require_permission
from app.core.security import hash_password
from app.crud.audit import write_audit_event
from app.crud.session import revoke_all_sessions
from app.crud.user import generate_admin_username, get_by_username
from app.database import get_db
from app.models.admin_profile import AdminProfile
from app.models.admin_role import SUPER_ADMIN_SLUG, AdminRole
from app.models.audit_log import AuthAuditLog, AuthEventType
from app.models.chat import Conversation, DirectMessage
from app.models.session import AuthSession
from app.models.user import RoleEnum, User
from app.schemas.admin_users import AdminUserOut, CreateAdminUserRequest, UpdateAdminUserRequest

router = APIRouter(prefix="/admin/admin-users", tags=["admin-users"])


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

    username = generate_admin_username(db, payload.full_name)
    user = User(
        username=username,
        password_hash=hash_password(payload.temp_password),
        full_name=payload.full_name,
        role=RoleEnum.admin,
        admin_role_id=role.id,
        must_change_password=True,
    )
    db.add(user)
    db.flush()
    db.add(AdminProfile(user_id=user.id))
    db.commit()
    db.refresh(user)

    ip, ua = _client_meta(request)
    write_audit_event(
        db, AuthEventType.admin_user_created, ip, ua, user=actor,
        detail=f"Created admin {username} ({role.name})", module="admin_users", target=username, status="success",
        actor_role=(actor.admin_role.slug if actor.admin_role else actor.role.value),
    )

    return _admin_out(user)


@router.patch("/{admin_id}", response_model=AdminUserOut)
def update_admin_user(
    admin_id: int,
    payload: UpdateAdminUserRequest,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("admin_users.edit")),
):
    user = _get_admin_or_404(db, admin_id)
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

        # Only a Super Admin actor may assign or remove the Super Admin role.
        is_super_admin_change = (
            (old_role is not None and old_role.slug == SUPER_ADMIN_SLUG)
            or new_role.slug == SUPER_ADMIN_SLUG
        )
        if is_super_admin_change and actor.admin_role.slug != SUPER_ADMIN_SLUG:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only a Super Admin can assign or remove the Super Admin role.",
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
        write_audit_event(
            db, AuthEventType.admin_user_role_changed, ip, ua, user=actor,
            detail=f"Changed {user.username}'s role from {old_role.name if old_role else 'none'} to {new_role.name}",
            module="admin_users", target=user.username, status="success", actor_role=actor_role_snapshot,
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
        write_audit_event(
            db, AuthEventType.admin_user_enabled if payload.is_active else AuthEventType.admin_user_disabled,
            ip, ua, user=actor, detail=f"{'Enabled' if payload.is_active else 'Disabled'} admin {user.username}",
            module="admin_users", target=user.username, status="success", actor_role=actor_role_snapshot,
        )

    if payload.reset_temp_password is not None:
        user.password_hash = hash_password(payload.reset_temp_password)
        user.must_change_password = True
        user.failed_login_count = 0
        user.locked_until = None
        revoke_all_sessions(db, user.id)
        write_audit_event(
            db, AuthEventType.admin_user_password_reset, ip, ua, user=actor,
            detail=f"Reset password for admin {user.username}", module="admin_users", target=user.username,
            status="success", actor_role=actor_role_snapshot,
        )

    if payload.full_name is not None or payload.email is not None:
        write_audit_event(
            db, AuthEventType.admin_user_updated, ip, ua, user=actor, detail=f"Edited admin {user.username}",
            module="admin_users", target=user.username, status="success", actor_role=actor_role_snapshot,
        )

    db.add(user)
    if profile is not None:
        db.add(profile)
    db.commit()
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
    revoke_all_sessions(db, user.id)
    db.commit()

    ip, ua = _client_meta(request)
    write_audit_event(
        db, AuthEventType.session_revoked, ip, ua, user=actor, detail=f"Revoked all sessions for admin {user.username}",
        module="admin_users", target=user.username, status="success",
        actor_role=(actor.admin_role.slug if actor.admin_role else actor.role.value),
    )
    return None


@router.delete("/{admin_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_admin_user(
    admin_id: int,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("admin_users.delete")),
):
    user = _get_admin_or_404(db, admin_id)

    if user.id == actor.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You cannot delete your own account.")

    if user.admin_role and user.admin_role.slug == SUPER_ADMIN_SLUG:
        if _count_active_super_admins(db, exclude_user_id=user.id) < 1:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot delete the last Super Admin.")

    username = user.username
    ip, ua = _client_meta(request)
    actor_role_snapshot = actor.admin_role.slug if actor.admin_role else actor.role.value

    # Detach (don't delete) audit history so security records survive account
    # removal — auth_audit_log.user_id is nullable for exactly this reason.
    db.execute(update(AuthAuditLog).where(AuthAuditLog.user_id == user.id).values(user_id=None))
    db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))

    # Chat rows have no ON DELETE CASCADE to users.id, so any conversation
    # this account took part in (as either side) must be cleared first.
    convo_ids = db.scalars(
        select(Conversation.id).where(or_(Conversation.user_a_id == user.id, Conversation.user_b_id == user.id))
    ).all()
    if convo_ids:
        db.execute(delete(DirectMessage).where(DirectMessage.conversation_id.in_(convo_ids)))
        db.execute(delete(Conversation).where(Conversation.id.in_(convo_ids)))

    db.delete(user)  # cascades to admin_profile via the User.admin_profile relationship
    db.commit()

    write_audit_event(
        db, AuthEventType.admin_user_deleted, ip, ua, user=actor, detail=f"Deleted admin {username}",
        module="admin_users", target=username, status="success", actor_role=actor_role_snapshot,
    )
    return None
