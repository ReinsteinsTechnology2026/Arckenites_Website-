from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.deps import require_permission
from app.crud.audit import write_audit_event
from app.crud.system_settings import get_settings, update_settings
from app.database import get_db
from app.models.audit_log import AuthEventType
from app.models.user import User
from app.schemas.system_settings import SystemSettingsOut, UpdateSystemSettingsRequest

router = APIRouter(prefix="/admin/settings", tags=["admin-settings"])


def _client_meta(request: Request) -> tuple[str, str]:
    ip = request.client.host if request.client else "unknown"
    ua = request.headers.get("user-agent", "unknown")
    return ip, ua


@router.get("", response_model=SystemSettingsOut)
def read_settings(db: Session = Depends(get_db), _actor: User = Depends(require_permission("settings.view"))):
    return get_settings(db)


@router.patch("", response_model=SystemSettingsOut)
def edit_settings(
    payload: UpdateSystemSettingsRequest,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("settings.edit")),
):
    before = get_settings(db)
    changed_fields = {
        k: v for k, v in payload.model_dump(exclude_unset=True).items()
        if v is not None and getattr(before, k) != v
    }

    updated = update_settings(db, updated_by_id=actor.id, **payload.model_dump(exclude_unset=True))

    if changed_fields:
        ip, ua = _client_meta(request)
        change_summary = ", ".join(f"{k}={v}" for k, v in changed_fields.items())
        write_audit_event(
            db, AuthEventType.settings_changed, ip, ua, user=actor, detail=f"Changed settings: {change_summary}",
            module="settings", target="system_settings", status="success",
        )

    return updated
