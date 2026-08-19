from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import require_role
from app.database import get_db
from app.models.audit_log import AuthAuditLog, AuthEventType
from app.models.user import User
from app.schemas.dashboard import (
    ActivityEntry,
    ActivityResponse,
    DashboardStatsResponse,
    RoleCount,
)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/stats", response_model=DashboardStatsResponse)
def stats(db: Session = Depends(get_db), _admin: User = Depends(require_role("admin"))):
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = now - timedelta(days=7)

    total_users = db.scalar(select(func.count()).select_from(User))
    role_rows = db.execute(select(User.role, func.count()).group_by(User.role)).all()
    active_users = db.scalar(select(func.count()).where(User.is_active.is_(True)))
    locked_accounts = db.scalar(
        select(func.count()).where(User.locked_until.is_not(None), User.locked_until > now)
    )
    new_users = db.scalar(select(func.count()).where(User.created_at >= week_ago))
    logins_today = db.scalar(
        select(func.count()).where(
            AuthAuditLog.event_type == AuthEventType.login_success,
            AuthAuditLog.created_at >= today_start,
        )
    )
    failed_logins_today = db.scalar(
        select(func.count()).where(
            AuthAuditLog.event_type.in_([AuthEventType.login_failure, AuthEventType.account_locked]),
            AuthAuditLog.created_at >= today_start,
        )
    )

    return DashboardStatsResponse(
        total_users=total_users,
        users_by_role=[RoleCount(role=r.value, count=c) for r, c in role_rows],
        active_users=active_users,
        inactive_users=total_users - active_users,
        locked_accounts=locked_accounts,
        new_users_last_7_days=new_users,
        logins_today=logins_today,
        failed_logins_today=failed_logins_today,
        generated_at=now,
    )


_MESSAGES = {
    AuthEventType.login_success: "{name} logged in",
    AuthEventType.login_failure: "Failed login attempt for {name}",
    AuthEventType.logout: "{name} logged out",
    AuthEventType.password_change: "{name} changed their password",
    AuthEventType.account_locked: "{name}'s account was locked after repeated failed attempts",
    AuthEventType.rate_limited: "Rate limit triggered for {name}",
}


@router.get("/activity", response_model=ActivityResponse)
def activity(
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_role("admin")),
):
    rows = db.execute(
        select(AuthAuditLog, User.full_name)
        .outerjoin(User, AuthAuditLog.user_id == User.id)
        .order_by(AuthAuditLog.created_at.desc())
        .limit(limit)
    ).all()

    items = []
    for log, full_name in rows:
        name = full_name or log.username_attempted or "unknown user"
        template = _MESSAGES.get(log.event_type, "{name}: " + log.event_type.value)
        items.append(
            ActivityEntry(
                id=log.id,
                event_type=log.event_type.value,
                actor_name=name,
                message=template.format(name=name),
                created_at=log.created_at,
            )
        )
    return ActivityResponse(items=items)
