from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from openpyxl import Workbook
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload
import io

from app.core.deps import require_permission
from app.core.uploads import MAX_FILES_PER_MESSAGE, delete_upload, get_upload_path, save_upload
from app.crud.audit import write_audit_event
from app.crud.support import (
    client_meta,
    attachment_out,
    message_out,
    ticket_detail_out,
    ticket_has_unread_for_admin,
    ticket_list_item_out,
)
from app.database import get_db
from app.models.audit_log import AuthEventType
from app.models.support import (
    SenderTypeEnum,
    SupportAttachment,
    SupportMessage,
    SupportTicket,
    TicketCategoryEnum,
    TicketPriorityEnum,
    TicketStatusEnum,
)
from app.models.user import RoleEnum, User
from app.schemas.support import (
    AssignTicketRequest,
    AttachmentOut,
    ChangePriorityRequest,
    ChangeStatusRequest,
    SendMessageRequest,
    TicketDetailOut,
    TicketListItemOut,
    TicketMessageOut,
    TicketStatsOut,
)

router = APIRouter(prefix="/admin/support", tags=["admin-support"])

# Allowed status transitions via the generic status-change endpoint.
# "closed" is reachable only via the dedicated close action, and leaving
# "closed" is only possible via the dedicated reopen action — matches the
# spec's "do not allow random invalid status transitions" requirement.
_ALLOWED_TRANSITIONS: dict[TicketStatusEnum, set[TicketStatusEnum]] = {
    TicketStatusEnum.open: {TicketStatusEnum.in_progress, TicketStatusEnum.waiting_for_student, TicketStatusEnum.resolved},
    TicketStatusEnum.in_progress: {TicketStatusEnum.waiting_for_student, TicketStatusEnum.resolved, TicketStatusEnum.open},
    TicketStatusEnum.waiting_for_student: {TicketStatusEnum.in_progress, TicketStatusEnum.resolved},
    TicketStatusEnum.resolved: {TicketStatusEnum.in_progress, TicketStatusEnum.waiting_for_student, TicketStatusEnum.open},
    TicketStatusEnum.closed: set(),
}


def _get_ticket_or_404(db: Session, ticket_id: int) -> SupportTicket:
    ticket = db.execute(
        select(SupportTicket)
        .options(selectinload(SupportTicket.messages).selectinload(SupportMessage.attachments))
        .options(selectinload(SupportTicket.messages).selectinload(SupportMessage.sender))
        .where(SupportTicket.id == ticket_id)
    ).scalar_one_or_none()
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    return ticket


@router.get("/stats", response_model=TicketStatsOut)
def get_stats(db: Session = Depends(get_db), _actor: User = Depends(require_permission("support.view"))):
    tickets = db.scalars(select(SupportTicket).options(selectinload(SupportTicket.messages))).all()
    return TicketStatsOut(
        total=len(tickets),
        open=sum(1 for t in tickets if t.status == TicketStatusEnum.open),
        in_progress=sum(1 for t in tickets if t.status == TicketStatusEnum.in_progress),
        waiting_for_student=sum(1 for t in tickets if t.status == TicketStatusEnum.waiting_for_student),
        resolved=sum(1 for t in tickets if t.status == TicketStatusEnum.resolved),
        unread=sum(1 for t in tickets if ticket_has_unread_for_admin(t)),
    )


def _apply_filters(query, search, status_, category, priority, assigned_to, date_from, date_to):
    if search:
        like = f"%{search}%"
        query = query.where(or_(SupportTicket.ticket_number.ilike(like), SupportTicket.subject.ilike(like)))
    if status_:
        query = query.where(SupportTicket.status == TicketStatusEnum(status_))
    if category:
        query = query.where(SupportTicket.category == TicketCategoryEnum(category))
    if priority:
        query = query.where(SupportTicket.priority == TicketPriorityEnum(priority))
    if assigned_to:
        query = query.where(SupportTicket.assigned_to == assigned_to)
    if date_from:
        query = query.where(SupportTicket.created_at >= date_from)
    if date_to:
        query = query.where(SupportTicket.created_at <= date_to)
    return query


@router.get("/tickets", response_model=list[TicketListItemOut])
def list_tickets(
    search: str = "",
    status_filter: str = "",
    category: str = "",
    priority: str = "",
    assigned_to: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("support.view")),
):
    query = select(SupportTicket).options(selectinload(SupportTicket.messages)).order_by(SupportTicket.updated_at.desc())
    query = _apply_filters(query, search, status_filter, category, priority, assigned_to, date_from, date_to)
    tickets = db.scalars(query).all()
    return [ticket_list_item_out(t, for_admin=True) for t in tickets]


@router.get("/tickets/export")
def export_tickets(
    search: str = "",
    status_filter: str = "",
    category: str = "",
    priority: str = "",
    assigned_to: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("support.export")),
):
    query = select(SupportTicket).order_by(SupportTicket.created_at.desc())
    query = _apply_filters(query, search, status_filter, category, priority, assigned_to, date_from, date_to)
    tickets = db.scalars(query).all()

    wb = Workbook()
    ws = wb.active
    ws.title = "Support Tickets"
    ws.append(["Ticket ID", "Requester", "Subject", "Category", "Priority", "Status", "Assigned To", "Created", "Updated"])
    for t in tickets:
        ws.append([
            t.ticket_number, t.requester.full_name, t.subject, t.category.value, t.priority.value, t.status.value,
            t.assignee.full_name if t.assignee else "", t.created_at.strftime("%Y-%m-%d %H:%M"),
            t.updated_at.strftime("%Y-%m-%d %H:%M"),
        ])

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=support_tickets.xlsx"},
    )


@router.get("/assignable-admins", response_model=list[dict])
def assignable_admins(db: Session = Depends(get_db), _actor: User = Depends(require_permission("support.view"))):
    rows = db.scalars(
        select(User).where(User.role == RoleEnum.admin, User.is_active.is_(True)).order_by(User.full_name)
    ).all()
    return [{"id": u.id, "full_name": u.full_name} for u in rows]


@router.get("/tickets/{ticket_id}", response_model=TicketDetailOut)
def get_ticket(
    ticket_id: int,
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("support.view")),
):
    ticket = _get_ticket_or_404(db, ticket_id)
    ticket.admin_last_read_at = datetime.now(timezone.utc)
    db.add(ticket)
    db.commit()
    ticket = _get_ticket_or_404(db, ticket_id)
    return ticket_detail_out(ticket, include_internal=True)


@router.post("/tickets/{ticket_id}/messages", response_model=TicketMessageOut, status_code=201)
def reply_to_ticket(
    ticket_id: int,
    payload: SendMessageRequest,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("support.reply")),
):
    ticket = _get_ticket_or_404(db, ticket_id)
    message = SupportMessage(
        ticket_id=ticket.id, sender_id=actor.id, sender_type=SenderTypeEnum.admin,
        message=payload.message, is_internal=False,
    )
    db.add(message)
    if ticket.status == TicketStatusEnum.open:
        ticket.status = TicketStatusEnum.in_progress
    db.add(ticket)
    db.commit()
    db.refresh(message)

    ip, ua = client_meta(request)
    write_audit_event(
        db, AuthEventType.support_ticket_replied, ip, ua, user=actor,
        detail=f"Replied to ticket {ticket.ticket_number}", module="support", target=ticket.ticket_number, status="success",
    )
    return message_out(message)


@router.post("/tickets/{ticket_id}/notes", response_model=TicketMessageOut, status_code=201)
def add_internal_note(
    ticket_id: int,
    payload: SendMessageRequest,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("support.add_internal_note")),
):
    ticket = _get_ticket_or_404(db, ticket_id)
    message = SupportMessage(
        ticket_id=ticket.id, sender_id=actor.id, sender_type=SenderTypeEnum.admin,
        message=payload.message, is_internal=True,
    )
    db.add(message)
    db.commit()
    db.refresh(message)

    ip, ua = client_meta(request)
    write_audit_event(
        db, AuthEventType.support_ticket_internal_note_added, ip, ua, user=actor,
        detail=f"Added internal note to ticket {ticket.ticket_number}", module="support",
        target=ticket.ticket_number, status="success",
    )
    return message_out(message)


@router.post("/tickets/{ticket_id}/messages/{message_id}/attachments", response_model=AttachmentOut, status_code=201)
async def upload_ticket_attachment(
    ticket_id: int,
    message_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("support.reply")),
    file: UploadFile = File(...),
):
    ticket = _get_ticket_or_404(db, ticket_id)
    message = next((m for m in ticket.messages if m.id == message_id and m.sender_id == actor.id), None)
    if message is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")
    if len(message.attachments) >= MAX_FILES_PER_MESSAGE:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Maximum {MAX_FILES_PER_MESSAGE} attachments per message.")

    contents = await file.read()
    storage_reference, file_name, file_type, file_size = save_upload(file, contents)

    attachment = SupportAttachment(
        ticket_id=ticket.id, message_id=message.id, file_name=file_name,
        file_type=file_type, file_size=file_size, storage_reference=storage_reference,
    )
    db.add(attachment)
    db.commit()
    db.refresh(attachment)
    return attachment_out(attachment)


@router.get("/tickets/{ticket_id}/attachments/{attachment_id}")
def download_ticket_attachment(
    ticket_id: int,
    attachment_id: int,
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("support.view")),
):
    ticket = _get_ticket_or_404(db, ticket_id)
    attachment = next((a for m in ticket.messages for a in m.attachments if a.id == attachment_id), None)
    if attachment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")

    path = get_upload_path(attachment.storage_reference)
    return FileResponse(path, media_type=attachment.file_type, filename=attachment.file_name)


@router.patch("/tickets/{ticket_id}/status", response_model=TicketDetailOut)
def change_status(
    ticket_id: int,
    payload: ChangeStatusRequest,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("support.change_status")),
):
    ticket = _get_ticket_or_404(db, ticket_id)
    new_status = TicketStatusEnum(payload.status)
    if new_status == TicketStatusEnum.closed:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Use the close action to close a ticket.")
    if new_status not in _ALLOWED_TRANSITIONS[ticket.status]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot move a ticket from {ticket.status.value} to {new_status.value}.",
        )

    old_status = ticket.status
    ticket.status = new_status
    ticket.resolved_at = datetime.now(timezone.utc) if new_status == TicketStatusEnum.resolved else None
    db.add(ticket)
    db.commit()

    ip, ua = client_meta(request)
    write_audit_event(
        db, AuthEventType.support_ticket_status_changed, ip, ua, user=actor,
        detail=f"Changed status of ticket {ticket.ticket_number} from {old_status.value} to {new_status.value}",
        module="support", target=ticket.ticket_number, status="success",
    )
    return ticket_detail_out(_get_ticket_or_404(db, ticket_id), include_internal=True)


@router.patch("/tickets/{ticket_id}/priority", response_model=TicketDetailOut)
def change_priority(
    ticket_id: int,
    payload: ChangePriorityRequest,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("support.change_priority")),
):
    ticket = _get_ticket_or_404(db, ticket_id)
    old_priority = ticket.priority
    ticket.priority = TicketPriorityEnum(payload.priority)
    db.add(ticket)
    db.commit()

    ip, ua = client_meta(request)
    write_audit_event(
        db, AuthEventType.support_ticket_priority_changed, ip, ua, user=actor,
        detail=f"Changed priority of ticket {ticket.ticket_number} from {old_priority.value} to {ticket.priority.value}",
        module="support", target=ticket.ticket_number, status="success",
    )
    return ticket_detail_out(_get_ticket_or_404(db, ticket_id), include_internal=True)


@router.patch("/tickets/{ticket_id}/assign", response_model=TicketDetailOut)
def assign_ticket(
    ticket_id: int,
    payload: AssignTicketRequest,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("support.assign")),
):
    ticket = _get_ticket_or_404(db, ticket_id)

    if payload.assigned_to is not None:
        assignee = db.get(User, payload.assigned_to)
        if assignee is None or assignee.role != RoleEnum.admin:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="assigned_to must reference an existing admin.")

    ticket.assigned_to = payload.assigned_to
    db.add(ticket)
    db.commit()

    ip, ua = client_meta(request)
    assignee_name = ticket.assignee.full_name if ticket.assignee else "nobody"
    write_audit_event(
        db, AuthEventType.support_ticket_assigned, ip, ua, user=actor,
        detail=f"Assigned ticket {ticket.ticket_number} to {assignee_name}",
        module="support", target=ticket.ticket_number, status="success",
    )
    return ticket_detail_out(_get_ticket_or_404(db, ticket_id), include_internal=True)


@router.post("/tickets/{ticket_id}/close", response_model=TicketDetailOut)
def close_ticket(
    ticket_id: int,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("support.close")),
):
    ticket = _get_ticket_or_404(db, ticket_id)
    if ticket.status == TicketStatusEnum.closed:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ticket is already closed.")

    ticket.status = TicketStatusEnum.closed
    ticket.closed_at = datetime.now(timezone.utc)
    db.add(ticket)
    db.commit()

    ip, ua = client_meta(request)
    write_audit_event(
        db, AuthEventType.support_ticket_closed, ip, ua, user=actor,
        detail=f"Closed ticket {ticket.ticket_number}", module="support", target=ticket.ticket_number, status="success",
    )
    return ticket_detail_out(_get_ticket_or_404(db, ticket_id), include_internal=True)


@router.post("/tickets/{ticket_id}/reopen", response_model=TicketDetailOut)
def reopen_ticket(
    ticket_id: int,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("support.change_status")),
):
    ticket = _get_ticket_or_404(db, ticket_id)
    if ticket.status != TicketStatusEnum.closed:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only a closed ticket can be reopened.")

    ticket.status = TicketStatusEnum.in_progress
    ticket.closed_at = None
    db.add(ticket)
    db.commit()

    ip, ua = client_meta(request)
    write_audit_event(
        db, AuthEventType.support_ticket_reopened, ip, ua, user=actor,
        detail=f"Reopened ticket {ticket.ticket_number}", module="support", target=ticket.ticket_number, status="success",
    )
    return ticket_detail_out(_get_ticket_or_404(db, ticket_id), include_internal=True)


@router.delete("/tickets/{ticket_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_ticket(
    ticket_id: int,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("support.delete")),
):
    ticket = _get_ticket_or_404(db, ticket_id)
    ticket_number = ticket.ticket_number

    for message in ticket.messages:
        for attachment in message.attachments:
            delete_upload(attachment.storage_reference)

    db.delete(ticket)
    db.commit()

    ip, ua = client_meta(request)
    write_audit_event(
        db, AuthEventType.support_ticket_deleted, ip, ua, user=actor,
        detail=f"Deleted ticket {ticket_number}", module="support", target=ticket_number, status="success",
    )
    return None
