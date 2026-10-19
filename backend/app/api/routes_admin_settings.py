from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import require_permission
from app.crud.audit import write_audit_event
from app.crud.system_settings import get_settings, update_settings
from app.database import get_db
from app.models.audit_log import AuthEventType
from app.models.system_settings import SETTINGS_ROW_ID, SystemSettings
from app.models.user import User
from app.schemas.system_settings import SystemSettingsOut, UpdateSystemSettingsRequest

router = APIRouter(prefix="/admin/settings", tags=["admin-settings"])


class JobApplicationsSectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


class JobApplicationsSectionOut(BaseModel):
    enabled: bool


def _client_meta(request: Request) -> tuple[str, str]:
    ip = request.client.host if request.client else "unknown"
    ua = request.headers.get("user-agent", "unknown")
    return ip, ua


@router.get("/job-applications-section", response_model=JobApplicationsSectionOut)
def read_job_applications_section(
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("job_applications.section_manage")),
):
    return {"enabled": get_settings(db).job_applications_section_enabled}


@router.patch("/job-applications-section", response_model=JobApplicationsSectionOut)
def edit_job_applications_section(
    payload: JobApplicationsSectionInput,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("job_applications.section_manage")),
):
    get_settings(db)
    row = db.scalar(select(SystemSettings).where(SystemSettings.id == SETTINGS_ROW_ID)
                    .with_for_update().execution_options(populate_existing=True))
    if row.job_applications_section_enabled != payload.enabled:
        row.job_applications_section_enabled = payload.enabled
        row.updated_by_id = actor.id
        ip, ua = _client_meta(request)
        write_audit_event(
            db, AuthEventType.settings_changed, ip, ua, user=actor,
            detail=f"Job Applications Section {'enabled' if payload.enabled else 'disabled'}",
            module="Job Applications", target="system_settings:job_applications_section_enabled",
            status="success", commit=False,
        )
        db.commit()
    return {"enabled": row.job_applications_section_enabled}


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
