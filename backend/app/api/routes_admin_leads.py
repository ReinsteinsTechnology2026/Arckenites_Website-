import io
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.deps import require_permission
from app.database import get_db
from app.models.lead import Lead, LeadStatusEnum
from app.models.user import User
from app.schemas.leads import CreateLeadRequest, LeadOut, UpdateLeadRequest

router = APIRouter(prefix="/admin", tags=["admin-leads"])


def _lead_out(lead: Lead) -> LeadOut:
    return LeadOut(
        id=lead.id, full_name=lead.full_name, phone=lead.phone, email=lead.email, source=lead.source,
        status=lead.status.value, notes=lead.notes,
        created_by_name=lead.created_by.full_name if lead.created_by else None,
        created_at=lead.created_at, updated_at=lead.updated_at,
    )


def _get_lead_or_404(db: Session, lead_id: int) -> Lead:
    lead = db.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")
    return lead


@router.post("/leads", response_model=LeadOut, status_code=201)
def create_lead(
    payload: CreateLeadRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("leads.create")),
):
    lead = Lead(
        full_name=payload.full_name, phone=payload.phone, email=payload.email,
        source=payload.source, notes=payload.notes, created_by_id=actor.id,
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)
    return _lead_out(lead)


@router.get("/leads", response_model=list[LeadOut])
def list_leads(
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("leads.view")),
):
    leads = db.scalars(
        select(Lead).options(selectinload(Lead.created_by)).order_by(Lead.created_at.desc())
    ).all()
    return [_lead_out(lead) for lead in leads]


@router.get("/leads/export")
def export_leads(
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("leads.export")),
):
    leads = db.scalars(
        select(Lead).options(selectinload(Lead.created_by)).order_by(Lead.created_at.desc())
    ).all()

    wb = Workbook()
    ws = wb.active
    ws.title = "Leads"
    ws.append(["Full Name", "Phone", "Email", "Source", "Status", "Notes", "Added By", "Created At"])

    for lead in leads:
        ws.append([
            lead.full_name,
            lead.phone or "",
            lead.email or "",
            lead.source or "",
            lead.status.value,
            lead.notes or "",
            lead.created_by.full_name if lead.created_by else "",
            lead.created_at.strftime("%Y-%m-%d %H:%M") if lead.created_at else "",
        ])

    for col in ws.columns:
        width = max(len(str(cell.value)) for cell in col if cell.value is not None) if col else 10
        ws.column_dimensions[col[0].column_letter].width = min(max(width + 2, 12), 40)

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    filename = f"arckenites-leads-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}.xlsx"
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.patch("/leads/{lead_id}", response_model=LeadOut)
def update_lead(
    lead_id: int,
    payload: UpdateLeadRequest,
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("leads.edit")),
):
    lead = _get_lead_or_404(db, lead_id)

    if payload.full_name is not None:
        lead.full_name = payload.full_name
    if payload.phone is not None:
        lead.phone = payload.phone or None
    if payload.email is not None:
        lead.email = payload.email or None
    if payload.source is not None:
        lead.source = payload.source or None
    if payload.status is not None:
        lead.status = LeadStatusEnum(payload.status)
    if payload.notes is not None:
        lead.notes = payload.notes or None

    db.add(lead)
    db.commit()
    db.refresh(lead)
    return _lead_out(lead)


@router.delete("/leads/{lead_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_lead(
    lead_id: int,
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("leads.delete")),
):
    lead = _get_lead_or_404(db, lead_id)
    db.delete(lead)
    db.commit()
    return None
