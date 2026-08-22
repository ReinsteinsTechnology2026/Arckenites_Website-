from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.support import SupportAttachment, SupportMessage, SupportTicket
from app.models.user import User
from app.schemas.support import AttachmentOut, TicketDetailOut, TicketListItemOut, TicketMessageOut


def client_meta(request: Request) -> tuple[str, str]:
    ip = request.client.host if request.client else "unknown"
    ua = request.headers.get("user-agent", "unknown")
    return ip, ua


def attachment_out(a: SupportAttachment) -> AttachmentOut:
    return AttachmentOut(id=a.id, file_name=a.file_name, file_type=a.file_type, file_size=a.file_size, created_at=a.created_at)


def message_out(m: SupportMessage) -> TicketMessageOut:
    return TicketMessageOut(
        id=m.id, sender_id=m.sender_id, sender_name=m.sender.full_name, sender_type=m.sender_type.value,
        message=m.message, is_internal=m.is_internal, created_at=m.created_at,
        attachments=[attachment_out(a) for a in m.attachments],
    )


def ticket_has_unread_for_requester(ticket: SupportTicket) -> bool:
    """Unread from the requester's point of view: any non-internal message
    NOT sent by the requester (i.e. from the admin/support team) newer than
    their last-read timestamp — same "count things newer than my last-read
    timestamp" model the chat system already uses."""
    for m in ticket.messages:
        if m.is_internal or m.sender_id == ticket.requester_id:
            continue
        if ticket.requester_last_read_at is None or m.created_at > ticket.requester_last_read_at:
            return True
    return False


def ticket_has_unread_for_admin(ticket: SupportTicket) -> bool:
    """Unread from the shared admin inbox's point of view: any message sent
    BY the requester newer than admin_last_read_at. Internal notes are
    always authored by an admin (sender_id != requester_id), so they're
    already excluded by this same check — no extra is_internal filter
    needed."""
    for m in ticket.messages:
        if m.sender_id != ticket.requester_id:
            continue
        if ticket.admin_last_read_at is None or m.created_at > ticket.admin_last_read_at:
            return True
    return False


def ticket_list_item_out(ticket: SupportTicket, *, for_admin: bool) -> TicketListItemOut:
    unread = ticket_has_unread_for_admin(ticket) if for_admin else ticket_has_unread_for_requester(ticket)
    return TicketListItemOut(
        id=ticket.id, ticket_number=ticket.ticket_number, requester_id=ticket.requester_id,
        requester_name=ticket.requester.full_name, requester_role=ticket.requester.role.value,
        subject=ticket.subject, category=ticket.category.value, priority=ticket.priority.value,
        status=ticket.status.value, assigned_to=ticket.assigned_to,
        assigned_to_name=ticket.assignee.full_name if ticket.assignee else None,
        created_at=ticket.created_at, updated_at=ticket.updated_at, unread=unread,
    )


def ticket_detail_out(ticket: SupportTicket, *, include_internal: bool) -> TicketDetailOut:
    messages = ticket.messages if include_internal else [m for m in ticket.messages if not m.is_internal]
    return TicketDetailOut(
        id=ticket.id, ticket_number=ticket.ticket_number, requester_id=ticket.requester_id,
        requester_name=ticket.requester.full_name, requester_username=ticket.requester.username,
        requester_role=ticket.requester.role.value, subject=ticket.subject,
        category=ticket.category.value, priority=ticket.priority.value, status=ticket.status.value,
        assigned_to=ticket.assigned_to, assigned_to_name=ticket.assignee.full_name if ticket.assignee else None,
        created_at=ticket.created_at, updated_at=ticket.updated_at, resolved_at=ticket.resolved_at,
        closed_at=ticket.closed_at, messages=[message_out(m) for m in messages],
    )


def get_own_ticket_or_404(db: Session, ticket_id: int, user: User) -> SupportTicket:
    ticket = db.execute(
        select(SupportTicket)
        .options(selectinload(SupportTicket.messages).selectinload(SupportMessage.attachments))
        .options(selectinload(SupportTicket.messages).selectinload(SupportMessage.sender))
        .where(SupportTicket.id == ticket_id, SupportTicket.requester_id == user.id)
    ).scalar_one_or_none()
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    return ticket
