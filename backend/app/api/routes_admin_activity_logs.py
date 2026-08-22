import csv
import io
from datetime import date, datetime, time, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from starlette.responses import StreamingResponse

from app.core.deps import require_permission
from app.database import get_db
from app.models.audit_log import AuthAuditLog
from app.models.user import User
from app.schemas.activity_logs import ActivityLogEntryOut, ActivityLogListOut

router = APIRouter(prefix="/admin/activity-logs", tags=["admin-activity-logs"])


def _apply_filters(
    query,
    *,
    q: str | None,
    user_id: int | None,
    role: str | None,
    module: str | None,
    action: str | None,
    status_: str | None,
    date_from: date | None,
    date_to: date | None,
):
    if q:
        like = f"%{q}%"
        query = query.where(
            or_(
                AuthAuditLog.username_attempted.ilike(like),
                AuthAuditLog.detail.ilike(like),
                AuthAuditLog.target.ilike(like),
            )
        )
    if user_id is not None:
        query = query.where(AuthAuditLog.user_id == user_id)
    if role:
        query = query.where(AuthAuditLog.actor_role == role)
    if module:
        query = query.where(AuthAuditLog.module == module)
    if action:
        query = query.where(AuthAuditLog.event_type == action)
    if status_:
        query = query.where(AuthAuditLog.status == status_)
    if date_from:
        query = query.where(AuthAuditLog.created_at >= datetime.combine(date_from, time.min, tzinfo=timezone.utc))
    if date_to:
        query = query.where(AuthAuditLog.created_at <= datetime.combine(date_to, time.max, tzinfo=timezone.utc))
    return query


def _row_out(log: AuthAuditLog, full_name: str | None) -> ActivityLogEntryOut:
    return ActivityLogEntryOut(
        id=log.id, created_at=log.created_at, user_id=log.user_id,
        user_name=full_name, username=log.username_attempted, role=log.actor_role,
        event_type=log.event_type.value, module=log.module, target=log.target,
        status=log.status, ip_address=log.ip_address, detail=log.detail,
    )


@router.get("", response_model=ActivityLogListOut)
def list_activity_logs(
    q: str | None = None,
    user_id: int | None = None,
    role: str | None = None,
    module: str | None = None,
    action: str | None = None,
    status_: str | None = Query(default=None, alias="status"),
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("activity_logs.view")),
):
    base = select(AuthAuditLog, User.full_name).outerjoin(User, AuthAuditLog.user_id == User.id)
    base = _apply_filters(
        base, q=q, user_id=user_id, role=role, module=module, action=action,
        status_=status_, date_from=date_from, date_to=date_to,
    )

    count_query = base.with_only_columns(AuthAuditLog.id)
    total = len(db.execute(count_query).all())

    rows = db.execute(
        base.order_by(AuthAuditLog.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()

    items = [_row_out(log, name) for log, name in rows]
    return ActivityLogListOut(items=items, total=total, page=page, page_size=page_size)


@router.get("/export")
def export_activity_logs(
    q: str | None = None,
    user_id: int | None = None,
    role: str | None = None,
    module: str | None = None,
    action: str | None = None,
    status_: str | None = Query(default=None, alias="status"),
    date_from: date | None = None,
    date_to: date | None = None,
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("activity_logs.export")),
):
    base = select(AuthAuditLog, User.full_name).outerjoin(User, AuthAuditLog.user_id == User.id)
    base = _apply_filters(
        base, q=q, user_id=user_id, role=role, module=module, action=action,
        status_=status_, date_from=date_from, date_to=date_to,
    )
    rows = db.execute(base.order_by(AuthAuditLog.created_at.desc()).limit(5000)).all()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Date/Time (UTC)", "User", "Role", "Action", "Module", "Target", "Status", "IP Address", "Details"])
    for log, full_name in rows:
        writer.writerow([
            log.created_at.isoformat(), full_name or log.username_attempted or "", log.actor_role or "",
            log.event_type.value, log.module or "", log.target or "", log.status or "", log.ip_address or "",
            log.detail or "",
        ])
    buffer.seek(0)

    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=activity_logs.csv"},
    )
