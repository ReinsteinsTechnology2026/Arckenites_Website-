from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.deps import require_role
from app.core.uploads import MAX_FILES_PER_MESSAGE, get_upload_path, save_upload
from app.core.video import generate_batch_room_name
from app.core.ws_manager import manager
from app.crud.audit import write_audit_event
from app.crud.support import (
    attachment_out,
    get_own_ticket_or_404,
    message_out,
    ticket_detail_out,
    ticket_list_item_out,
)
from app.database import get_db
from app.models.audit_log import AuthEventType
from app.models.batch import Batch, BatchEnrollment
from app.models.batch_chat import BatchChatReadState, BatchMessage
from app.models.batch_resources import ClassVideo, LabAccess, StudyMaterial
from app.models.class_session import ClassSession
from app.models.interview_schedule import InterviewSchedule
from app.models.student import CurrentRoleEnum
from app.models.support import SenderTypeEnum, SupportAttachment, SupportMessage, SupportTicket, TicketPriorityEnum, TicketStatusEnum
from app.models.user import User
from app.schemas.admin_students import CompleteProfileRequest
from app.schemas.auth import MeResponse
from app.schemas.batch_chat import BatchMessageOut, SendBatchMessageRequest
from app.schemas.batch_resources import ClassVideoOut, LabAccessOut, StudyMaterialOut
from app.schemas.class_sessions import ClassSessionOut
from app.schemas.interview_schedule import InterviewOut
from app.schemas.student_batches import StudentBatchCardOut
from app.schemas.support import (
    AttachmentOut,
    CreateTicketRequest,
    SendMessageRequest,
    TicketDetailOut,
    TicketListItemOut,
    TicketMessageOut,
)
from app.schemas.video import VideoRoomOut

router = APIRouter(prefix="/students", tags=["students"])


def _client_meta(request: Request) -> tuple[str, str]:
    ip = request.client.host if request.client else "unknown"
    ua = request.headers.get("user-agent", "unknown")
    return ip, ua


@router.post("/me/profile", response_model=MeResponse)
def complete_profile(
    payload: CompleteProfileRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("student")),
):
    user.full_name = payload.full_name

    profile = user.student_profile
    profile.phone = payload.mobile_number
    profile.email = payload.email
    profile.current_role = CurrentRoleEnum(payload.current_role)

    db.add(user)
    db.add(profile)
    db.commit()
    db.refresh(user)

    return MeResponse.model_validate(user)


@router.get("/me/schedule", response_model=list[ClassSessionOut])
def my_schedule(
    db: Session = Depends(get_db),
    user: User = Depends(require_role("student")),
):
    """Every scheduled class across every batch this student is currently
    allocated to, soonest first. Not gated to any particular program here —
    the dashboard decides which students see this section; any student who
    calls it gets their real batch schedule."""
    rows = db.execute(
        select(ClassSession)
        .join(BatchEnrollment, BatchEnrollment.batch_id == ClassSession.batch_id)
        .where(BatchEnrollment.student_id == user.id)
        .order_by(ClassSession.session_date, ClassSession.start_time)
    ).scalars().all()

    batch_names = {s.batch_id: s.batch.name for s in rows}
    return [
        ClassSessionOut(
            id=s.id, batch_id=s.batch_id, batch_name=batch_names[s.batch_id],
            title=s.title, session_date=s.session_date, start_time=s.start_time,
            end_time=s.end_time, meeting_link=s.meeting_link, notes=s.notes, created_at=s.created_at,
        )
        for s in rows
    ]


@router.get("/me/schedule/{session_id}/video", response_model=VideoRoomOut)
def my_schedule_video_room(
    session_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("student")),
):
    """This batch's one permanent video room — every class this batch has
    opens the same room. 404 (not a leaked room name) if the session
    doesn't belong to a batch this student is enrolled in — same
    authorization join as my_schedule."""
    row = db.execute(
        select(ClassSession)
        .join(BatchEnrollment, BatchEnrollment.batch_id == ClassSession.batch_id)
        .where(ClassSession.id == session_id, BatchEnrollment.student_id == user.id)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Class session not found")

    return VideoRoomOut(
        room_name=generate_batch_room_name(row.batch_id),
        display_name=user.full_name,
        subject=row.batch.name,
    )


def _unread_chat_count(db: Session, batch_id: int, user_id: int) -> int:
    read_state = db.scalar(
        select(BatchChatReadState).where(BatchChatReadState.batch_id == batch_id, BatchChatReadState.user_id == user_id)
    )
    filters = [BatchMessage.batch_id == batch_id, BatchMessage.sender_id != user_id]
    if read_state is not None:
        filters.append(BatchMessage.created_at > read_state.last_read_at)
    return db.scalar(select(func.count()).select_from(BatchMessage).where(*filters)) or 0


def _student_batch_card_out(db: Session, batch: Batch, user_id: int) -> StudentBatchCardOut:
    return StudentBatchCardOut(
        id=batch.id, name=batch.name, status=batch.status.value,
        program_name=batch.program.name if batch.program else None, batch_type=batch.batch_type.value,
        start_date=batch.start_date, unread_chat_count=_unread_chat_count(db, batch.id, user_id),
    )


@router.get("/me/batches", response_model=list[StudentBatchCardOut])
def my_batches(db: Session = Depends(get_db), user: User = Depends(require_role("student"))):
    """Every batch this student is enrolled in — the meeting room for each
    exists automatically (no scheduled class required), so this backs a
    join-anytime list independent of the class schedule."""
    rows = db.execute(
        select(Batch)
        .join(BatchEnrollment, BatchEnrollment.batch_id == Batch.id)
        .where(BatchEnrollment.student_id == user.id)
        .order_by(Batch.name)
    ).scalars().all()
    return [_student_batch_card_out(db, b, user.id) for b in rows]


@router.get("/me/batches/{batch_id}/video", response_model=VideoRoomOut)
def my_batch_video_room(
    batch_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("student")),
):
    """Direct access to a batch's permanent room by batch id, not gated on
    any class session existing — the room is there as soon as the batch is."""
    enrollment = db.execute(
        select(BatchEnrollment).where(BatchEnrollment.batch_id == batch_id, BatchEnrollment.student_id == user.id)
    ).scalar_one_or_none()
    if enrollment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Batch not found")

    batch = db.get(Batch, batch_id)
    return VideoRoomOut(room_name=generate_batch_room_name(batch.id), display_name=user.full_name, subject=batch.name)


def _get_own_enrolled_batch_or_404(db: Session, batch_id: int, user: User) -> Batch:
    enrollment = db.scalar(
        select(BatchEnrollment).where(BatchEnrollment.batch_id == batch_id, BatchEnrollment.student_id == user.id)
    )
    if enrollment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Batch not found")
    return db.get(Batch, batch_id)


def _batch_message_out(m: BatchMessage) -> BatchMessageOut:
    return BatchMessageOut(
        id=m.id, batch_id=m.batch_id, sender_id=m.sender_id, sender_name=m.sender.full_name,
        message=m.message, created_at=m.created_at,
    )


@router.get("/me/batches/{batch_id}/chat/messages", response_model=list[BatchMessageOut])
def get_my_batch_chat(batch_id: int, db: Session = Depends(get_db), user: User = Depends(require_role("student"))):
    """This batch's shared group chat — every message, marks the student's
    own read-state so their unread badge clears."""
    _get_own_enrolled_batch_or_404(db, batch_id, user)
    rows = db.scalars(
        select(BatchMessage).where(BatchMessage.batch_id == batch_id).order_by(BatchMessage.created_at)
    ).all()

    read_state = db.scalar(
        select(BatchChatReadState).where(BatchChatReadState.batch_id == batch_id, BatchChatReadState.user_id == user.id)
    )
    now = datetime.now(timezone.utc)
    if read_state is None:
        db.add(BatchChatReadState(batch_id=batch_id, user_id=user.id, last_read_at=now))
    else:
        read_state.last_read_at = now
        db.add(read_state)
    db.commit()

    return [_batch_message_out(m) for m in rows]


@router.post("/me/batches/{batch_id}/chat/messages", response_model=BatchMessageOut, status_code=201)
async def send_my_batch_chat(
    batch_id: int,
    payload: SendBatchMessageRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("student")),
):
    batch = _get_own_enrolled_batch_or_404(db, batch_id, user)
    message = BatchMessage(batch_id=batch_id, sender_id=user.id, message=payload.message)
    db.add(message)
    db.commit()
    db.refresh(message)
    out = _batch_message_out(message)

    student_ids = db.scalars(select(BatchEnrollment.student_id).where(BatchEnrollment.batch_id == batch_id)).all()
    recipients = set(student_ids)
    if batch.trainer_id:
        recipients.add(batch.trainer_id)
    for recipient_id in recipients:
        await manager.send_to_user(recipient_id, {"type": "batch_message", "batch_id": batch_id, "message": out.model_dump(mode="json")})

    return out


@router.get("/me/lab-access", response_model=list[LabAccessOut])
def my_lab_access(db: Session = Depends(get_db), user: User = Depends(require_role("student"))):
    rows = db.execute(
        select(LabAccess)
        .join(BatchEnrollment, BatchEnrollment.batch_id == LabAccess.batch_id)
        .where(BatchEnrollment.student_id == user.id)
        .order_by(LabAccess.created_at.desc())
    ).scalars().all()
    return [
        LabAccessOut(
            id=r.id, batch_id=r.batch_id, batch_name=r.batch.name, title=r.title, access_url=r.access_url,
            username=r.username, password=r.password, notes=r.notes, created_at=r.created_at,
        )
        for r in rows
    ]


@router.get("/me/videos", response_model=list[ClassVideoOut])
def my_videos(db: Session = Depends(get_db), user: User = Depends(require_role("student"))):
    rows = db.execute(
        select(ClassVideo)
        .join(BatchEnrollment, BatchEnrollment.batch_id == ClassVideo.batch_id)
        .where(BatchEnrollment.student_id == user.id)
        .order_by(ClassVideo.created_at.desc())
    ).scalars().all()
    return [
        ClassVideoOut(
            id=r.id, batch_id=r.batch_id, batch_name=r.batch.name, title=r.title,
            video_url=r.video_url, description=r.description, created_at=r.created_at,
        )
        for r in rows
    ]


@router.get("/me/materials", response_model=list[StudyMaterialOut])
def my_materials(db: Session = Depends(get_db), user: User = Depends(require_role("student"))):
    rows = db.execute(
        select(StudyMaterial)
        .join(BatchEnrollment, BatchEnrollment.batch_id == StudyMaterial.batch_id)
        .where(BatchEnrollment.student_id == user.id)
        .order_by(StudyMaterial.created_at.desc())
    ).scalars().all()
    return [
        StudyMaterialOut(
            id=r.id, batch_id=r.batch_id, batch_name=r.batch.name, title=r.title,
            file_url=r.file_url, description=r.description, created_at=r.created_at,
        )
        for r in rows
    ]


@router.get("/me/interviews", response_model=list[InterviewOut])
def my_interviews(db: Session = Depends(get_db), user: User = Depends(require_role("student"))):
    rows = db.scalars(
        select(InterviewSchedule)
        .where(InterviewSchedule.student_id == user.id)
        .order_by(InterviewSchedule.interview_date)
    ).all()
    return [
        InterviewOut(
            id=i.id, student_id=i.student_id, student_name=user.full_name, student_username=user.username,
            company_name=i.company_name, role=i.role, interview_date=i.interview_date,
            interview_time=i.interview_time, mode=i.mode.value, location_or_link=i.location_or_link,
            status=i.status.value, notes=i.notes, created_at=i.created_at,
        )
        for i in rows
    ]


# ---------------------------------------------------------------------------
# Support tickets
# ---------------------------------------------------------------------------

@router.get("/me/tickets", response_model=list[TicketListItemOut])
def my_tickets(db: Session = Depends(get_db), user: User = Depends(require_role("student"))):
    tickets = db.execute(
        select(SupportTicket)
        .options(selectinload(SupportTicket.messages))
        .where(SupportTicket.requester_id == user.id)
        .order_by(SupportTicket.updated_at.desc())
    ).scalars().all()
    return [ticket_list_item_out(t, for_admin=False) for t in tickets]


@router.post("/me/tickets", response_model=TicketDetailOut, status_code=201)
def create_ticket(
    payload: CreateTicketRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("student")),
):
    ticket = SupportTicket(
        ticket_number="PENDING", requester_id=user.id, subject=payload.subject,
        category=payload.category, priority=TicketPriorityEnum(payload.priority), status=TicketStatusEnum.open,
    )
    db.add(ticket)
    db.flush()
    ticket.ticket_number = f"SUP-{ticket.id:06d}"

    first_message = SupportMessage(
        ticket_id=ticket.id, sender_id=user.id, sender_type=SenderTypeEnum.student,
        message=payload.description, is_internal=False,
    )
    db.add(first_message)
    ticket.requester_last_read_at = first_message.created_at
    db.commit()

    ip, ua = _client_meta(request)
    write_audit_event(
        db, AuthEventType.support_ticket_created, ip, ua, user=user,
        detail=f"Created ticket {ticket.ticket_number}: {ticket.subject}",
        module="support", target=ticket.ticket_number, status="success",
    )

    ticket = get_own_ticket_or_404(db, ticket.id, user)
    return ticket_detail_out(ticket, include_internal=False)


@router.get("/me/tickets/{ticket_id}", response_model=TicketDetailOut)
def get_ticket(ticket_id: int, db: Session = Depends(get_db), user: User = Depends(require_role("student"))):
    ticket = get_own_ticket_or_404(db, ticket_id, user)
    ticket.requester_last_read_at = datetime.now(timezone.utc)
    db.add(ticket)
    db.commit()

    return ticket_detail_out(ticket, include_internal=False)


@router.post("/me/tickets/{ticket_id}/messages", response_model=TicketMessageOut, status_code=201)
def reply_to_ticket(
    ticket_id: int,
    payload: SendMessageRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("student")),
):
    ticket = get_own_ticket_or_404(db, ticket_id, user)
    if ticket.status == TicketStatusEnum.closed:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This ticket is closed. Reopen it to reply.")

    message = SupportMessage(
        ticket_id=ticket.id, sender_id=user.id, sender_type=SenderTypeEnum.student,
        message=payload.message, is_internal=False,
    )
    db.add(message)
    if ticket.status == TicketStatusEnum.waiting_for_student:
        ticket.status = TicketStatusEnum.in_progress
    db.add(ticket)
    db.commit()
    db.refresh(message)
    return message_out(message)


@router.post("/me/tickets/{ticket_id}/messages/{message_id}/attachments", response_model=AttachmentOut, status_code=201)
async def upload_ticket_attachment(
    ticket_id: int,
    message_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("student")),
    file: UploadFile = File(...),
):
    ticket = get_own_ticket_or_404(db, ticket_id, user)
    message = next((m for m in ticket.messages if m.id == message_id and m.sender_id == user.id), None)
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


@router.get("/me/tickets/{ticket_id}/attachments/{attachment_id}")
def download_ticket_attachment(
    ticket_id: int,
    attachment_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("student")),
):
    ticket = get_own_ticket_or_404(db, ticket_id, user)
    attachment = next(
        (a for m in ticket.messages if not m.is_internal for a in m.attachments if a.id == attachment_id), None
    )
    if attachment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")

    path = get_upload_path(attachment.storage_reference)
    return FileResponse(path, media_type=attachment.file_type, filename=attachment.file_name)


@router.post("/me/tickets/{ticket_id}/reopen", response_model=TicketDetailOut)
def reopen_ticket(
    ticket_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("student")),
):
    ticket = get_own_ticket_or_404(db, ticket_id, user)
    if ticket.status != TicketStatusEnum.closed:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only a closed ticket can be reopened.")

    ticket.status = TicketStatusEnum.in_progress
    ticket.closed_at = None
    db.add(ticket)
    db.commit()

    ip, ua = _client_meta(request)
    write_audit_event(
        db, AuthEventType.support_ticket_reopened, ip, ua, user=user,
        detail=f"Student reopened ticket {ticket.ticket_number}", module="support", target=ticket.ticket_number, status="success",
    )

    return get_ticket(ticket_id, db, user)
