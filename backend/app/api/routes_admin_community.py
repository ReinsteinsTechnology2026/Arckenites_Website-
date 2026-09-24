import io
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.deps import require_permission
from app.crud.audit import write_audit_event
from app.database import get_db
from app.models.audit_log import AuthEventType
from app.models.user import RoleEnum, User
from app.schemas.community import CommunityMemberOut, UpdateCommunityMemberRequest


def _client_meta(request: Request) -> tuple[str, str]:
    ip = request.client.host if request.client else "unknown"
    ua = request.headers.get("user-agent", "unknown")
    return ip, ua

router = APIRouter(prefix="/admin", tags=["admin-community"])


def _member_out(user: User) -> CommunityMemberOut:
    profile = user.community_profile
    return CommunityMemberOut(
        id=user.id,
        full_name=user.full_name,
        mobile_number=profile.mobile_number if profile else None,
        email=profile.email if profile else None,
        email_verified=bool(profile and profile.email_verified_at),
        is_active=user.is_active,
        created_at=user.created_at,
    )


@router.get("/community", response_model=list[CommunityMemberOut])
def list_community_members(
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("community.view")),
):
    users = db.scalars(
        select(User)
        .where(User.role == RoleEnum.community)
        .options(selectinload(User.community_profile))
        .order_by(User.created_at.desc())
    ).all()
    return [_member_out(u) for u in users]


@router.patch("/community/{member_id}", response_model=CommunityMemberOut)
def update_community_member(
    member_id: int,
    payload: UpdateCommunityMemberRequest,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("community.edit")),
):
    """Enable/disable only — a Community member's own name/mobile/email
    were self-submitted and verified by them; nothing here lets an admin
    silently rewrite those."""
    user = db.get(User, member_id)
    if user is None or user.role != RoleEnum.community:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Community member not found")

    ip, ua = _client_meta(request)
    if payload.is_active != user.is_active:
        user.is_active = payload.is_active
        db.add(user)
        db.commit()
        db.refresh(user)
        write_audit_event(
            db,
            AuthEventType.community_member_enabled if payload.is_active else AuthEventType.community_member_disabled,
            ip, ua, user=actor, module="community", target=user.username, status="success",
        )

    return _member_out(user)


@router.get("/community/export")
def export_community_members(
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("community.export")),
):
    users = db.scalars(
        select(User)
        .where(User.role == RoleEnum.community)
        .options(selectinload(User.community_profile))
        .order_by(User.created_at.desc())
    ).all()

    wb = Workbook()
    ws = wb.active
    ws.title = "Community"
    ws.append(["Full Name", "Mobile Number", "Email", "Email Verified", "Status", "Joined"])

    for u in users:
        profile = u.community_profile
        ws.append([
            u.full_name,
            profile.mobile_number if profile else "",
            profile.email if profile else "",
            "Verified" if (profile and profile.email_verified_at) else "Unverified",
            "Active" if u.is_active else "Inactive",
            u.created_at.strftime("%Y-%m-%d %H:%M") if u.created_at else "",
        ])

    for col in ws.columns:
        width = max(len(str(cell.value)) for cell in col if cell.value is not None) if col else 10
        ws.column_dimensions[col[0].column_letter].width = min(max(width + 2, 12), 40)

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    filename = f"arckenites-community-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}.xlsx"
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
