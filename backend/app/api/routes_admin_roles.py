import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.deps import require_permission
from app.crud.audit import write_audit_event
from app.crud.permissions import PERMISSION_CATALOG, PERMISSION_KEYS
from app.database import get_db
from app.models.admin_role import SUPER_ADMIN_SLUG, AdminRole, AdminRolePermission
from app.models.audit_log import AuthEventType
from app.models.user import User
from app.schemas.admin_roles import (
    CreateRoleRequest,
    PermissionOut,
    RoleDetailOut,
    RoleOut,
    UpdateRoleRequest,
)

router = APIRouter(prefix="/admin", tags=["admin-roles"])

logger = logging.getLogger("roles")

# The one safe, generic message shown to the browser when a role-permission
# save fails at the database level (e.g. a permission key the code accepts
# doesn't yet exist as a row in this database's permission catalog — see
# PERMISSION_SAVE_ERROR docstring on create_role/update_role for the full
# story). Never includes the underlying SQL/constraint detail.
PERMISSION_SAVE_ERROR = "Could not save one or more of the selected permissions. Please try again, or contact an administrator if this keeps happening."


def _client_meta(request: Request) -> tuple[str, str]:
    ip = request.client.host if request.client else "unknown"
    ua = request.headers.get("user-agent", "unknown")
    return ip, ua


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return slug or "role"


def _role_out(db: Session, role: AdminRole) -> RoleOut:
    user_count = db.scalar(select(func.count()).select_from(User).where(User.admin_role_id == role.id)) or 0
    if role.slug == SUPER_ADMIN_SLUG:
        permission_count = len(PERMISSION_KEYS)
    else:
        permission_count = db.scalar(
            select(func.count()).select_from(AdminRolePermission).where(AdminRolePermission.admin_role_id == role.id)
        ) or 0
    return RoleOut(
        id=role.id, name=role.name, slug=role.slug, description=role.description, is_system=role.is_system,
        user_count=user_count, permission_count=permission_count, created_at=role.created_at,
    )


def _get_role_or_404(db: Session, role_id: int) -> AdminRole:
    role = db.get(AdminRole, role_id)
    if role is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")
    return role


@router.get("/roles", response_model=list[RoleOut])
def list_roles(db: Session = Depends(get_db), _actor: User = Depends(require_permission("roles.view"))):
    roles = db.scalars(select(AdminRole).order_by(AdminRole.id)).all()
    return [_role_out(db, r) for r in roles]


@router.get("/permissions/catalog", response_model=list[PermissionOut])
def permission_catalog(_actor: User = Depends(require_permission("roles.view"))):
    return [PermissionOut(**p) for p in PERMISSION_CATALOG]


@router.get("/roles/{role_id}", response_model=RoleDetailOut)
def get_role(role_id: int, db: Session = Depends(get_db), _actor: User = Depends(require_permission("roles.view"))):
    role = _get_role_or_404(db, role_id)
    base = _role_out(db, role)
    if role.slug == SUPER_ADMIN_SLUG:
        granted = sorted(PERMISSION_KEYS)
    else:
        granted = sorted(
            db.scalars(
                select(AdminRolePermission.permission_key).where(AdminRolePermission.admin_role_id == role.id)
            ).all()
        )
    return RoleDetailOut(**base.model_dump(), granted_permission_keys=granted)


@router.post("/roles", response_model=RoleDetailOut, status_code=201)
def create_role(
    payload: CreateRoleRequest,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("roles.create")),
):
    slug = _slugify(payload.name)
    if db.scalar(select(AdminRole).where(AdminRole.slug == slug)) is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A role with that name already exists.")

    invalid = [k for k in payload.permission_keys if k not in PERMISSION_KEYS]
    if invalid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown permission key(s): {', '.join(invalid)}")

    role = AdminRole(name=payload.name, slug=slug, description=payload.description, is_system=False)
    db.add(role)
    db.flush()
    for key in set(payload.permission_keys):
        db.add(AdminRolePermission(admin_role_id=role.id, permission_key=key))
    try:
        db.commit()
    except IntegrityError:
        # A key passed the PERMISSION_KEYS check above (the code-level
        # catalog) but has no matching row in this database's `permissions`
        # table yet — e.g. a new permission was shipped in code but the
        # database hasn't been synced with `python seed.py --rbac-only` yet
        # (see deploy.sh). Fail safely rather than leak the raw DB error.
        db.rollback()
        logger.exception("Failed to save permission grants while creating role %r", payload.name)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=PERMISSION_SAVE_ERROR)
    db.refresh(role)

    ip, ua = _client_meta(request)
    write_audit_event(
        db, AuthEventType.role_created, ip, ua, user=actor, detail=f"Created role {role.name}",
        module="roles", target=role.name, status="success",
    )

    return get_role(role.id, db, actor)


@router.patch("/roles/{role_id}", response_model=RoleDetailOut)
def update_role(
    role_id: int,
    payload: UpdateRoleRequest,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("roles.edit")),
):
    role = _get_role_or_404(db, role_id)
    if role.slug == SUPER_ADMIN_SLUG:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Super Admin always has full access and cannot be modified.",
        )
    if role.is_system and (payload.name is not None or payload.description is not None):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This role's name cannot be changed.")

    if payload.name is not None:
        role.name = payload.name
    if payload.description is not None:
        role.description = payload.description

    if payload.permission_keys is not None:
        invalid = [k for k in payload.permission_keys if k not in PERMISSION_KEYS]
        if invalid:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown permission key(s): {', '.join(invalid)}")
        db.execute(delete(AdminRolePermission).where(AdminRolePermission.admin_role_id == role.id))
        for key in set(payload.permission_keys):
            db.add(AdminRolePermission(admin_role_id=role.id, permission_key=key))

    db.add(role)
    try:
        db.commit()
    except IntegrityError:
        # Same story as create_role above — a permission key valid in code
        # but not yet present in this database's `permissions` table.
        db.rollback()
        logger.exception("Failed to save permission grants for role id=%s", role_id)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=PERMISSION_SAVE_ERROR)
    db.refresh(role)

    ip, ua = _client_meta(request)
    event = AuthEventType.permission_changed if payload.permission_keys is not None else AuthEventType.role_updated
    write_audit_event(
        db, event, ip, ua, user=actor, detail=f"Updated role {role.name}",
        module="roles", target=role.name, status="success",
    )

    return get_role(role.id, db, actor)


@router.delete("/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_role(
    role_id: int,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("roles.delete")),
):
    role = _get_role_or_404(db, role_id)
    if role.is_system:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Built-in roles cannot be deleted.")

    assigned = db.scalar(select(func.count()).select_from(User).where(User.admin_role_id == role.id)) or 0
    if assigned > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot delete: {assigned} admin(s) are still assigned to this role. Reassign them first.",
        )

    role_name = role.name
    db.execute(delete(AdminRolePermission).where(AdminRolePermission.admin_role_id == role.id))
    db.delete(role)
    db.commit()

    ip, ua = _client_meta(request)
    write_audit_event(
        db, AuthEventType.role_deleted, ip, ua, user=actor, detail=f"Deleted role {role_name}",
        module="roles", target=role_name, status="success",
    )
    return None
