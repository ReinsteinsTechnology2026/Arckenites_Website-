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
from app.crud.profile import to_public_profile
from app.crud.support import (
    client_meta,
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
from app.models.batch_resources import ClassVideo, StudyMaterial
from app.models.class_session import ClassSession
from app.models.support import SenderTypeEnum, SupportAttachment, SupportMessage, SupportTicket, TicketPriorityEnum, TicketStatusEnum
from app.models.user import User
from app.schemas.auth import MeResponse, UpdateMyProfileRequest
from app.schemas.batch_chat import BatchMessageOut, SendBatchMessageRequest
from app.schemas.batch_members import BatchMembersOut
from app.schemas.batch_resources import ClassVideoOut, CreateClassVideoRequest, CreateStudyMaterialRequest, StudyMaterialOut
from app.schemas.class_sessions import ClassSessionOut, CreateClassSessionRequest, UpdateClassSessionRequest
from app.schemas.staff_batches import TrainerBatchCardOut
from app.schemas.support import (
    AttachmentOut,
    CreateTicketRequest,
    SendMessageRequest,
    TicketDetailOut,
    TicketListItemOut,
    TicketMessageOut,
)
from app.schemas.video import VideoRoomOut

router = APIRouter(prefix="/staff", tags=["staff"])


@router.patch("/me/profile", response_model=MeResponse)
def update_my_profile(
    payload: UpdateMyProfileRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("staff")),
):
    """The trainer's own "edit my profile" action from the Profile screen —
    trainers have no onboarding-completion endpoint the way students do, so
    this is the only self-service write to StaffProfile's contact fields."""
    user.full_name = payload.full_name

    profile = user.staff_profile
    profile.email = payload.email
    profile.phone = payload.phone
    profile.address = payload.address

    db.add(user)
    db.add(profile)
    db.commit()
    db.refresh(user)

    return MeResponse.model_validate(user)


@router.get("/me/schedule", response_model=list[ClassSessionOut])
def my_schedule(
    db: Session = Depends(get_db),
    user: User = Depends(require_role("staff")),
):
    """Every scheduled class across every batch this trainer is assigned to
    as the primary trainer, soonest first."""
    rows = db.execute(
        select(ClassSession)
        .join(Batch, Batch.id == ClassSession.batch_id)
        .where(Batch.trainer_id == user.id)
        .order_by(ClassSession.session_date, ClassSession.start_time)
    ).scalars().all()

    return [
        ClassSessionOut(
            id=s.id, batch_id=s.batch_id, batch_name=s.batch.name,
            title=s.title, session_date=s.session_date, start_time=s.start_time,
            end_time=s.end_time, meeting_link=s.meeting_link, notes=s.notes, created_at=s.created_at,
        )
        for s in rows
    ]


@router.get("/me/schedule/{session_id}/video", response_model=VideoRoomOut)
def my_schedule_video_room(
    session_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("staff")),
):
    """This batch's one permanent video room — every class this batch has
    opens the same room. Same HMAC room-name formula as the student
    endpoint, so both land in the identical room."""
    row = db.execute(
        select(ClassSession)
        .join(Batch, Batch.id == ClassSession.batch_id)
        .where(ClassSession.id == session_id, Batch.trainer_id == user.id)
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


def _batch_card_out(db: Session, batch: Batch, user_id: int) -> TrainerBatchCardOut:
    student_count = db.scalar(
        select(func.count()).select_from(BatchEnrollment).where(BatchEnrollment.batch_id == batch.id)
    ) or 0
    return TrainerBatchCardOut(
        id=batch.id, name=batch.name, course=batch.course, batch_type=batch.batch_type.value,
        status=batch.status.value, program_name=batch.program.name if batch.program else None,
        student_count=student_count, max_capacity=batch.max_capacity, start_date=batch.start_date,
        unread_chat_count=_unread_chat_count(db, batch.id, user_id),
    )


@router.get("/me/batches", response_model=list[TrainerBatchCardOut])
def my_batches(db: Session = Depends(get_db), user: User = Depends(require_role("staff"))):
    """Every batch this trainer is assigned to, with enough context (program,
    student count, status, unread chat) to render as a card — the meeting
    room and chat for each exist automatically, so this backs a
    join-anytime list independent of the class schedule."""
    rows = db.execute(select(Batch).where(Batch.trainer_id == user.id).order_by(Batch.name)).scalars().all()
    return [_batch_card_out(db, b, user.id) for b in rows]


@router.get("/me/batches/{batch_id}", response_model=TrainerBatchCardOut)
def get_my_batch(batch_id: int, db: Session = Depends(get_db), user: User = Depends(require_role("staff"))):
    """Single-batch detail for the workspace header."""
    batch = _get_own_batch_or_404(db, batch_id, user)
    return _batch_card_out(db, batch, user.id)


@router.get("/me/batches/{batch_id}/video", response_model=VideoRoomOut)
def my_batch_video_room(
    batch_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("staff")),
):
    """Direct access to a batch's permanent room by batch id, not gated on
    any class session existing — the room is there as soon as the batch is."""
    batch = db.execute(
        select(Batch).where(Batch.id == batch_id, Batch.trainer_id == user.id)
    ).scalar_one_or_none()
    if batch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Batch not found")

    return VideoRoomOut(room_name=generate_batch_room_name(batch.id), display_name=user.full_name, subject=batch.name)


def _get_own_batch_or_404(db: Session, batch_id: int, user: User) -> Batch:
    batch = db.execute(
        select(Batch).where(Batch.id == batch_id, Batch.trainer_id == user.id)
    ).scalar_one_or_none()
    if batch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Batch not found")
    return batch


@router.get("/me/batches/{batch_id}/members", response_model=BatchMembersOut)
def get_my_batch_members(batch_id: int, db: Session = Depends(get_db), user: User = Depends(require_role("staff"))):
    """The trainer's own view of who's enrolled — photo + name only, same
    public shape a student would see, plus the trainer themself as trainer."""
    batch = _get_own_batch_or_404(db, batch_id, user)

    student_ids = db.scalars(select(BatchEnrollment.student_id).where(BatchEnrollment.batch_id == batch_id)).all()
    students = db.scalars(select(User).where(User.id.in_(student_ids)).order_by(User.full_name)).all()

    return BatchMembersOut(
        trainer=to_public_profile(user),
        students=[to_public_profile(s) for s in students],
    )


@router.get("/me/batches/{batch_id}/materials", response_model=list[StudyMaterialOut])
def list_my_batch_materials(
    batch_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("staff")),
):
    """Study materials this trainer has posted for one of their own
    batches — same table students see via /students/me/materials."""
    batch = _get_own_batch_or_404(db, batch_id, user)
    rows = db.scalars(
        select(StudyMaterial).where(StudyMaterial.batch_id == batch_id).order_by(StudyMaterial.created_at.desc())
    ).all()
    return [
        StudyMaterialOut(
            id=r.id, batch_id=r.batch_id, batch_name=batch.name,
            title=r.title, file_url=r.file_url, description=r.description, created_at=r.created_at,
        )
        for r in rows
    ]


@router.post("/me/batches/{batch_id}/materials", response_model=StudyMaterialOut, status_code=201)
def upload_batch_material(
    batch_id: int,
    payload: CreateStudyMaterialRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("staff")),
):
    """A trainer posting their own class notes/materials — only for a batch
    they're actually assigned to as trainer. Shows up immediately on every
    enrolled student's dashboard, since it's the same study_materials row
    /students/me/materials already reads."""
    batch = _get_own_batch_or_404(db, batch_id, user)
    entry = StudyMaterial(batch_id=batch_id, title=payload.title, file_url=payload.file_url, description=payload.description)
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return StudyMaterialOut(
        id=entry.id, batch_id=entry.batch_id, batch_name=batch.name,
        title=entry.title, file_url=entry.file_url, description=entry.description, created_at=entry.created_at,
    )


@router.delete("/me/batches/{batch_id}/materials/{material_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_batch_material(
    batch_id: int,
    material_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("staff")),
):
    _get_own_batch_or_404(db, batch_id, user)
    entry = db.get(StudyMaterial, material_id)
    if entry is None or entry.batch_id != batch_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Study material not found")
    db.delete(entry)
    db.commit()
    return None


@router.get("/me/batches/{batch_id}/videos", response_model=list[ClassVideoOut])
def list_my_batch_videos(
    batch_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("staff")),
):
    """Recorded class videos this trainer has posted for one of their own
    batches — same table students see via /students/me/videos."""
    batch = _get_own_batch_or_404(db, batch_id, user)
    rows = db.scalars(
        select(ClassVideo).where(ClassVideo.batch_id == batch_id).order_by(ClassVideo.created_at.desc())
    ).all()
    return [
        ClassVideoOut(
            id=r.id, batch_id=r.batch_id, batch_name=batch.name,
            title=r.title, video_url=r.video_url, description=r.description, created_at=r.created_at,
        )
        for r in rows
    ]


@router.post("/me/batches/{batch_id}/videos", response_model=ClassVideoOut, status_code=201)
def upload_batch_video(
    batch_id: int,
    payload: CreateClassVideoRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("staff")),
):
    """A trainer posting a recorded class (e.g. a screen recording, once
    uploaded to Drive/YouTube-unlisted/etc.) — only for a batch they're
    actually assigned to as trainer. Shows up immediately on every enrolled
    student's dashboard, same as study materials."""
    batch = _get_own_batch_or_404(db, batch_id, user)
    entry = ClassVideo(batch_id=batch_id, title=payload.title, video_url=payload.video_url, description=payload.description)
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return ClassVideoOut(
        id=entry.id, batch_id=entry.batch_id, batch_name=batch.name,
        title=entry.title, video_url=entry.video_url, description=entry.description, created_at=entry.created_at,
    )


@router.delete("/me/batches/{batch_id}/videos/{video_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_batch_video(
    batch_id: int,
    video_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("staff")),
):
    _get_own_batch_or_404(db, batch_id, user)
    entry = db.get(ClassVideo, video_id)
    if entry is None or entry.batch_id != batch_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Class video not found")
    db.delete(entry)
    db.commit()
    return None


# ---------------------------------------------------------------------------
# Class session CRUD — a trainer scheduling their own batch's classes.
# Mirrors the admin session endpoints in routes_admin_batches.py exactly,
# but scoped to "a batch this trainer actually owns" instead of an RBAC
# permission check. Every session created here is immediately visible on
# /students/me/schedule and /admin/class-schedule — same table, no extra
# wiring needed on the read side.
# ---------------------------------------------------------------------------

def _session_out(s: ClassSession, batch_name: str) -> ClassSessionOut:
    return ClassSessionOut(
        id=s.id, batch_id=s.batch_id, batch_name=batch_name, title=s.title, session_date=s.session_date,
        start_time=s.start_time, end_time=s.end_time, meeting_link=s.meeting_link, notes=s.notes, created_at=s.created_at,
    )


@router.get("/me/batches/{batch_id}/sessions", response_model=list[ClassSessionOut])
def list_my_batch_sessions(batch_id: int, db: Session = Depends(get_db), user: User = Depends(require_role("staff"))):
    batch = _get_own_batch_or_404(db, batch_id, user)
    rows = db.scalars(
        select(ClassSession).where(ClassSession.batch_id == batch_id).order_by(ClassSession.session_date, ClassSession.start_time)
    ).all()
    return [_session_out(s, batch.name) for s in rows]


@router.post("/me/batches/{batch_id}/sessions", response_model=ClassSessionOut, status_code=201)
def create_my_batch_session(
    batch_id: int,
    payload: CreateClassSessionRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("staff")),
):
    batch = _get_own_batch_or_404(db, batch_id, user)
    session = ClassSession(
        batch_id=batch_id, title=payload.title, session_date=payload.session_date,
        start_time=payload.start_time, end_time=payload.end_time, meeting_link=payload.meeting_link, notes=payload.notes,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return _session_out(session, batch.name)


def _get_my_session_or_404(db: Session, batch_id: int, session_id: int, user: User) -> ClassSession:
    _get_own_batch_or_404(db, batch_id, user)
    session = db.get(ClassSession, session_id)
    if session is None or session.batch_id != batch_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Class session not found")
    return session


@router.patch("/me/batches/{batch_id}/sessions/{session_id}", response_model=ClassSessionOut)
def update_my_batch_session(
    batch_id: int,
    session_id: int,
    payload: UpdateClassSessionRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("staff")),
):
    session = _get_my_session_or_404(db, batch_id, session_id, user)

    if payload.title is not None:
        session.title = payload.title
    if payload.session_date is not None:
        session.session_date = payload.session_date
    if payload.start_time is not None:
        session.start_time = payload.start_time
    if payload.end_time is not None:
        session.end_time = payload.end_time
    if payload.meeting_link is not None:
        session.meeting_link = payload.meeting_link
    if payload.notes is not None:
        session.notes = payload.notes

    db.add(session)
    db.commit()
    db.refresh(session)
    return _session_out(session, session.batch.name)


@router.delete("/me/batches/{batch_id}/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_my_batch_session(
    batch_id: int,
    session_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("staff")),
):
    session = _get_my_session_or_404(db, batch_id, session_id, user)
    db.delete(session)
    db.commit()
    return None


# ---------------------------------------------------------------------------
# Batch group chat — trainer + every enrolled student, one shared thread.
# Real-time delivery reuses the existing chat WebSocket connection (opened
# by chat.js on every dashboard page) via ws_manager.manager, rather than a
# second socket — send_to_user() is a no-op for anyone not currently
# connected, same as the 1:1 chat's live-push behavior.
# ---------------------------------------------------------------------------

def _batch_message_out(m: BatchMessage) -> BatchMessageOut:
    return BatchMessageOut(
        id=m.id, batch_id=m.batch_id, sender_id=m.sender_id, sender_name=m.sender.full_name,
        sender_photo_url=m.sender.photo_url, message=m.message, created_at=m.created_at,
    )


@router.get("/me/batches/{batch_id}/chat/messages", response_model=list[BatchMessageOut])
def get_my_batch_chat(batch_id: int, db: Session = Depends(get_db), user: User = Depends(require_role("staff"))):
    _get_own_batch_or_404(db, batch_id, user)
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
    user: User = Depends(require_role("staff")),
):
    _get_own_batch_or_404(db, batch_id, user)
    message = BatchMessage(batch_id=batch_id, sender_id=user.id, message=payload.message)
    db.add(message)
    db.commit()
    db.refresh(message)
    out = _batch_message_out(message)

    student_ids = db.scalars(select(BatchEnrollment.student_id).where(BatchEnrollment.batch_id == batch_id)).all()
    for recipient_id in [*student_ids, user.id]:
        await manager.send_to_user(recipient_id, {"type": "batch_message", "batch_id": batch_id, "message": out.model_dump(mode="json")})

    return out


# ---------------------------------------------------------------------------
# Support tickets
# ---------------------------------------------------------------------------

@router.get("/me/tickets", response_model=list[TicketListItemOut])
def my_tickets(db: Session = Depends(get_db), user: User = Depends(require_role("staff"))):
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
    user: User = Depends(require_role("staff")),
):
    ticket = SupportTicket(
        ticket_number="PENDING", requester_id=user.id, subject=payload.subject,
        category=payload.category, priority=TicketPriorityEnum(payload.priority), status=TicketStatusEnum.open,
    )
    db.add(ticket)
    db.flush()
    ticket.ticket_number = f"SUP-{ticket.id:06d}"

    first_message = SupportMessage(
        ticket_id=ticket.id, sender_id=user.id, sender_type=SenderTypeEnum.staff,
        message=payload.description, is_internal=False,
    )
    db.add(first_message)
    ticket.requester_last_read_at = first_message.created_at
    db.commit()

    ip, ua = client_meta(request)
    write_audit_event(
        db, AuthEventType.support_ticket_created, ip, ua, user=user,
        detail=f"Created ticket {ticket.ticket_number}: {ticket.subject}",
        module="support", target=ticket.ticket_number, status="success",
    )

    ticket = get_own_ticket_or_404(db, ticket.id, user)
    return ticket_detail_out(ticket, include_internal=False)


@router.get("/me/tickets/{ticket_id}", response_model=TicketDetailOut)
def get_ticket(ticket_id: int, db: Session = Depends(get_db), user: User = Depends(require_role("staff"))):
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
    user: User = Depends(require_role("staff")),
):
    ticket = get_own_ticket_or_404(db, ticket_id, user)
    if ticket.status == TicketStatusEnum.closed:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This ticket is closed. Reopen it to reply.")

    message = SupportMessage(
        ticket_id=ticket.id, sender_id=user.id, sender_type=SenderTypeEnum.staff,
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
    user: User = Depends(require_role("staff")),
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
    user: User = Depends(require_role("staff")),
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
    user: User = Depends(require_role("staff")),
):
    ticket = get_own_ticket_or_404(db, ticket_id, user)
    if ticket.status != TicketStatusEnum.closed:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only a closed ticket can be reopened.")

    ticket.status = TicketStatusEnum.in_progress
    ticket.closed_at = None
    db.add(ticket)
    db.commit()

    ip, ua = client_meta(request)
    write_audit_event(
        db, AuthEventType.support_ticket_reopened, ip, ua, user=user,
        detail=f"Trainer reopened ticket {ticket.ticket_number}", module="support", target=ticket.ticket_number, status="success",
    )

    return get_ticket(ticket_id, db, user)
