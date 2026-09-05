import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.deps import require_permission
from app.core.security import hash_password
from app.core.uploads import delete_recording, get_recording_path
from app.crud.audit import write_audit_event
from app.database import get_db
from app.models.audit_log import AuthEventType
from app.models.meeting import (
    Meeting,
    MeetingMessage,
    MeetingNote,
    MeetingParticipant,
    MeetingRecording,
    MeetingStatus,
    ParticipantRole,
    ParticipantStatus,
    RecordingStatus,
)
from app.models.user import RoleEnum, User
from app.schemas.meeting import (
    AddParticipantRequest,
    CreateMeetingRequest,
    MeetingDetailOut,
    MeetingListItemOut,
    MeetingStatsOut,
    MessageOut,
    NoteOut,
    ParticipantOut,
    RecordingOut,
    UpdateMeetingRequest,
)

router = APIRouter(prefix="/admin/meetings", tags=["admin-meetings"])


def _client_meta(request: Request) -> tuple[str, str]:
    ip = request.client.host if request.client else "unknown"
    ua = request.headers.get("user-agent", "unknown")
    return ip, ua


def _get_meeting_or_404(db: Session, meeting_id: int) -> Meeting:
    meeting = db.get(Meeting, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
    return meeting


def _participant_out(p: MeetingParticipant, name_by_user: dict[int, str]) -> ParticipantOut:
    return ParticipantOut(
        id=p.id, user_id=p.user_id, full_name=name_by_user.get(p.user_id, "Unknown"),
        role=p.role.value, status=p.status.value, joined_at=p.joined_at, left_at=p.left_at,
    )


@router.get("/stats", response_model=MeetingStatsOut)
def meeting_stats(db: Session = Depends(get_db), _actor: User = Depends(require_permission("meetings.view"))):
    total = db.scalar(select(func.count()).select_from(Meeting)) or 0
    scheduled = db.scalar(select(func.count()).select_from(Meeting).where(Meeting.status == MeetingStatus.SCHEDULED)) or 0
    live = db.scalar(select(func.count()).select_from(Meeting).where(Meeting.status == MeetingStatus.LIVE)) or 0
    completed = db.scalar(select(func.count()).select_from(Meeting).where(Meeting.status == MeetingStatus.COMPLETED)) or 0
    total_participants = db.scalar(select(func.count()).select_from(MeetingParticipant)) or 0
    return MeetingStatsOut(
        total_meetings=total, scheduled_meetings=scheduled, live_meetings=live,
        completed_meetings=completed, total_participants=total_participants,
    )


@router.get("", response_model=list[MeetingListItemOut])
def list_meetings(
    status_filter: str | None = None,
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("meetings.view")),
):
    query = select(Meeting).order_by(Meeting.scheduled_at.desc())
    if status_filter:
        try:
            query = query.where(Meeting.status == MeetingStatus(status_filter))
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid status filter.")
    meetings = db.scalars(query).all()

    host_ids = {m.host_id for m in meetings}
    hosts = {u.id: u.full_name for u in db.scalars(select(User).where(User.id.in_(host_ids))).all()} if host_ids else {}
    counts = dict(
        db.execute(
            select(MeetingParticipant.meeting_id, func.count())
            .where(MeetingParticipant.meeting_id.in_([m.id for m in meetings]))
            .group_by(MeetingParticipant.meeting_id)
        ).all()
    ) if meetings else {}

    return [
        MeetingListItemOut(
            id=m.id, meeting_token=m.meeting_token, title=m.title, host_name=hosts.get(m.host_id, "Unknown"),
            scheduled_at=m.scheduled_at, duration_minutes=m.duration_minutes, status=m.status.value,
            participant_count=counts.get(m.id, 0), has_password=m.password_hash is not None,
            recording_enabled=m.recording_enabled,
        )
        for m in meetings
    ]


@router.get("/lookup/hosts", response_model=list[dict])
def lookup_hosts(q: str = "", db: Session = Depends(get_db), _actor: User = Depends(require_permission("meetings.create"))):
    """Admins and staff can be meeting hosts."""
    query = select(User).where(User.role.in_([RoleEnum.admin, RoleEnum.staff]), User.is_active.is_(True))
    if q:
        like = f"%{q}%"
        query = query.where(or_(User.username.ilike(like), User.full_name.ilike(like)))
    users = db.scalars(query.order_by(User.full_name).limit(50)).all()
    return [{"id": u.id, "full_name": u.full_name, "username": u.username, "role": u.role.value} for u in users]


@router.get("/lookup/participants", response_model=list[dict])
def lookup_participants(q: str = "", db: Session = Depends(get_db), _actor: User = Depends(require_permission("meetings.create"))):
    """Any registered, active user can be invited as a participant."""
    query = select(User).where(User.is_active.is_(True))
    if q:
        like = f"%{q}%"
        query = query.where(or_(User.username.ilike(like), User.full_name.ilike(like)))
    users = db.scalars(query.order_by(User.full_name).limit(50)).all()
    return [{"id": u.id, "full_name": u.full_name, "username": u.username, "role": u.role.value} for u in users]


@router.post("", response_model=MeetingDetailOut, status_code=status.HTTP_201_CREATED)
def create_meeting(
    payload: CreateMeetingRequest,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("meetings.create")),
):
    host = db.get(User, payload.host_id)
    if host is None or host.role not in (RoleEnum.admin, RoleEnum.staff):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Host must be an existing admin or staff account.")

    meeting = Meeting(
        title=payload.title, description=payload.description, host_id=payload.host_id,
        password_hash=hash_password(payload.password) if payload.password else None,
        scheduled_at=payload.scheduled_at, duration_minutes=payload.duration_minutes,
        max_participants=payload.max_participants,
        mic_enabled=payload.mic_enabled, camera_enabled=payload.camera_enabled,
        screen_share_enabled=payload.screen_share_enabled, chat_enabled=payload.chat_enabled,
        notes_enabled=payload.notes_enabled, participant_notes_enabled=payload.participant_notes_enabled,
        recording_enabled=payload.recording_enabled, waiting_room_enabled=payload.waiting_room_enabled,
        require_approval=payload.require_approval,
    )
    db.add(meeting)
    db.flush()

    db.add(MeetingParticipant(meeting_id=meeting.id, user_id=host.id, role=ParticipantRole.HOST, status=ParticipantStatus.INVITED))
    for uid in dict.fromkeys(payload.participant_user_ids):  # de-dupe, preserve order
        if uid == host.id:
            continue
        if db.get(User, uid) is None:
            continue
        db.add(MeetingParticipant(meeting_id=meeting.id, user_id=uid, role=ParticipantRole.PARTICIPANT, status=ParticipantStatus.INVITED))
    db.add(MeetingNote(meeting_id=meeting.id, content="", action_items="[]"))
    db.commit()

    ip, ua = _client_meta(request)
    write_audit_event(db, AuthEventType.meeting_created, ip, ua, user=actor, detail=f"Meeting created: {meeting.title}", module="meetings", target=meeting.meeting_token, status="success")

    return _meeting_detail(db, meeting)


def _meeting_detail(db: Session, meeting: Meeting) -> MeetingDetailOut:
    host = db.get(User, meeting.host_id)
    rows = db.scalars(select(MeetingParticipant).where(MeetingParticipant.meeting_id == meeting.id)).all()
    user_ids = {p.user_id for p in rows if p.user_id is not None}
    names = {u.id: u.full_name for u in db.scalars(select(User).where(User.id.in_(user_ids))).all()} if user_ids else {}

    return MeetingDetailOut(
        id=meeting.id, meeting_token=meeting.meeting_token, title=meeting.title, description=meeting.description,
        host_id=meeting.host_id, host_name=host.full_name if host else "Unknown",
        scheduled_at=meeting.scheduled_at, duration_minutes=meeting.duration_minutes, status=meeting.status.value,
        started_at=meeting.started_at, ended_at=meeting.ended_at, has_password=meeting.password_hash is not None,
        max_participants=meeting.max_participants,
        mic_enabled=meeting.mic_enabled, camera_enabled=meeting.camera_enabled,
        screen_share_enabled=meeting.screen_share_enabled, chat_enabled=meeting.chat_enabled,
        notes_enabled=meeting.notes_enabled, participant_notes_enabled=meeting.participant_notes_enabled,
        recording_enabled=meeting.recording_enabled, waiting_room_enabled=meeting.waiting_room_enabled,
        require_approval=meeting.require_approval,
        participants=[_participant_out(p, names) for p in rows],
    )


@router.get("/{meeting_id}", response_model=MeetingDetailOut)
def get_meeting(meeting_id: int, db: Session = Depends(get_db), _actor: User = Depends(require_permission("meetings.view"))):
    return _meeting_detail(db, _get_meeting_or_404(db, meeting_id))


@router.patch("/{meeting_id}", response_model=MeetingDetailOut)
def update_meeting(
    meeting_id: int, payload: UpdateMeetingRequest,
    db: Session = Depends(get_db), actor: User = Depends(require_permission("meetings.edit")),
):
    meeting = _get_meeting_or_404(db, meeting_id)
    if meeting.status in (MeetingStatus.COMPLETED, MeetingStatus.CANCELLED):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Can't edit a completed or cancelled meeting.")

    data = payload.model_dump(exclude_unset=True, exclude={"password", "clear_password"})
    if payload.host_id is not None:
        host = db.get(User, payload.host_id)
        if host is None or host.role not in (RoleEnum.admin, RoleEnum.staff):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Host must be an existing admin or staff account.")
    for key, value in data.items():
        setattr(meeting, key, value)
    if payload.clear_password:
        meeting.password_hash = None
    elif payload.password:
        meeting.password_hash = hash_password(payload.password)

    db.add(meeting)
    db.commit()
    return _meeting_detail(db, meeting)


@router.post("/{meeting_id}/start", response_model=MeetingDetailOut)
def start_meeting(meeting_id: int, request: Request, db: Session = Depends(get_db), actor: User = Depends(require_permission("meetings.manage"))):
    meeting = _get_meeting_or_404(db, meeting_id)
    if meeting.status != MeetingStatus.SCHEDULED:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Meeting is {meeting.status.value.lower()}, not scheduled.")
    meeting.status = MeetingStatus.LIVE
    meeting.started_at = datetime.now(timezone.utc)
    db.add(meeting)
    db.add(MeetingMessage(meeting_id=meeting.id, user_id=None, message=f"{actor.full_name} started the meeting.", is_system=True))
    db.commit()

    ip, ua = _client_meta(request)
    write_audit_event(db, AuthEventType.meeting_started, ip, ua, user=actor, detail=f"Meeting started: {meeting.title}", module="meetings", target=meeting.meeting_token, status="success")
    return _meeting_detail(db, meeting)


@router.post("/{meeting_id}/end", response_model=MeetingDetailOut)
def end_meeting(meeting_id: int, request: Request, db: Session = Depends(get_db), actor: User = Depends(require_permission("meetings.manage"))):
    meeting = _get_meeting_or_404(db, meeting_id)
    if meeting.status != MeetingStatus.LIVE:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Meeting isn't live.")
    now = datetime.now(timezone.utc)
    meeting.status = MeetingStatus.COMPLETED
    meeting.ended_at = now
    db.add(meeting)

    for p in db.scalars(select(MeetingParticipant).where(
        MeetingParticipant.meeting_id == meeting.id, MeetingParticipant.status == ParticipantStatus.JOINED
    )).all():
        p.status = ParticipantStatus.LEFT
        p.left_at = now
        db.add(p)

    for rec in db.scalars(select(MeetingRecording).where(
        MeetingRecording.meeting_id == meeting.id, MeetingRecording.status == RecordingStatus.RECORDING
    )).all():
        rec.status = RecordingStatus.FAILED  # meeting ended without a real capture pipeline completing it
        rec.completed_at = now
        db.add(rec)

    db.add(MeetingMessage(meeting_id=meeting.id, user_id=None, message=f"{actor.full_name} ended the meeting.", is_system=True))
    db.commit()

    ip, ua = _client_meta(request)
    write_audit_event(db, AuthEventType.meeting_ended, ip, ua, user=actor, detail=f"Meeting ended: {meeting.title}", module="meetings", target=meeting.meeting_token, status="success")
    return _meeting_detail(db, meeting)


@router.post("/{meeting_id}/cancel", response_model=MeetingDetailOut)
def cancel_meeting(meeting_id: int, request: Request, db: Session = Depends(get_db), actor: User = Depends(require_permission("meetings.edit"))):
    meeting = _get_meeting_or_404(db, meeting_id)
    if meeting.status not in (MeetingStatus.SCHEDULED,):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only a scheduled meeting can be cancelled.")
    meeting.status = MeetingStatus.CANCELLED
    db.add(meeting)
    db.commit()

    ip, ua = _client_meta(request)
    write_audit_event(db, AuthEventType.meeting_cancelled, ip, ua, user=actor, detail=f"Meeting cancelled: {meeting.title}", module="meetings", target=meeting.meeting_token, status="success")
    return _meeting_detail(db, meeting)


@router.delete("/{meeting_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_meeting(meeting_id: int, db: Session = Depends(get_db), actor: User = Depends(require_permission("meetings.delete"))):
    meeting = _get_meeting_or_404(db, meeting_id)
    if meeting.status == MeetingStatus.LIVE:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="End the meeting before deleting it.")

    for rec in db.scalars(select(MeetingRecording).where(MeetingRecording.meeting_id == meeting.id)).all():
        if rec.file_path:
            delete_recording(rec.file_path)
        db.delete(rec)
    db.query(MeetingMessage).filter(MeetingMessage.meeting_id == meeting.id).delete()
    db.query(MeetingNote).filter(MeetingNote.meeting_id == meeting.id).delete()
    db.query(MeetingParticipant).filter(MeetingParticipant.meeting_id == meeting.id).delete()
    db.delete(meeting)
    db.commit()
    return None


@router.post("/{meeting_id}/participants", response_model=ParticipantOut, status_code=status.HTTP_201_CREATED)
def add_participant(meeting_id: int, payload: AddParticipantRequest, db: Session = Depends(get_db), actor: User = Depends(require_permission("meetings.manage"))):
    meeting = _get_meeting_or_404(db, meeting_id)
    user = db.get(User, payload.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    already_invited = db.scalar(select(MeetingParticipant).where(
        MeetingParticipant.meeting_id == meeting_id, MeetingParticipant.user_id == payload.user_id
    ))
    if already_invited:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Already invited.")

    participant = MeetingParticipant(meeting_id=meeting_id, user_id=payload.user_id, role=ParticipantRole.PARTICIPANT, status=ParticipantStatus.INVITED)
    db.add(participant)
    db.commit()
    return _participant_out(participant, {user.id: user.full_name})


@router.delete("/{meeting_id}/participants/{participant_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_participant(meeting_id: int, participant_id: int, db: Session = Depends(get_db), actor: User = Depends(require_permission("meetings.manage"))):
    participant = db.get(MeetingParticipant, participant_id)
    if participant is None or participant.meeting_id != meeting_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Participant not found")
    participant.status = ParticipantStatus.REMOVED
    db.add(participant)
    db.commit()
    return None


@router.get("/{meeting_id}/messages", response_model=list[MessageOut])
def admin_meeting_messages(meeting_id: int, db: Session = Depends(get_db), _actor: User = Depends(require_permission("meetings.view"))):
    _get_meeting_or_404(db, meeting_id)
    rows = db.scalars(select(MeetingMessage).where(MeetingMessage.meeting_id == meeting_id).order_by(MeetingMessage.created_at)).all()
    user_ids = {m.user_id for m in rows if m.user_id is not None}
    names = {u.id: u.full_name for u in db.scalars(select(User).where(User.id.in_(user_ids))).all()} if user_ids else {}
    return [
        MessageOut(id=m.id, user_id=m.user_id, sender_name=names.get(m.user_id, "System"), message=m.message, is_system=m.is_system, created_at=m.created_at)
        for m in rows
    ]


@router.get("/{meeting_id}/notes", response_model=NoteOut)
def admin_meeting_notes(meeting_id: int, db: Session = Depends(get_db), _actor: User = Depends(require_permission("meetings.view"))):
    _get_meeting_or_404(db, meeting_id)
    note = db.scalar(select(MeetingNote).where(MeetingNote.meeting_id == meeting_id))
    if note is None:
        return NoteOut(content="", action_items=[], updated_by_name=None, updated_at=datetime.now(timezone.utc))
    author = db.get(User, note.updated_by) if note.updated_by else None
    return NoteOut(content=note.content, action_items=json.loads(note.action_items or "[]"), updated_by_name=author.full_name if author else None, updated_at=note.updated_at)


@router.get("/{meeting_id}/recordings", response_model=list[RecordingOut])
def admin_meeting_recordings(meeting_id: int, db: Session = Depends(get_db), _actor: User = Depends(require_permission("meetings.view"))):
    _get_meeting_or_404(db, meeting_id)
    rows = db.scalars(select(MeetingRecording).where(MeetingRecording.meeting_id == meeting_id).order_by(MeetingRecording.started_at.desc())).all()
    return [RecordingOut(id=r.id, status=r.status.value, file_path=r.file_path, duration_seconds=r.duration_seconds, file_size_bytes=r.file_size_bytes, started_at=r.started_at, completed_at=r.completed_at) for r in rows]


@router.post("/{meeting_id}/recordings/start", response_model=RecordingOut, status_code=status.HTTP_201_CREATED)
def start_recording(meeting_id: int, request: Request, db: Session = Depends(get_db), actor: User = Depends(require_permission("meetings.manage"))):
    meeting = _get_meeting_or_404(db, meeting_id)
    if meeting.status != MeetingStatus.LIVE:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Meeting isn't live.")
    if not meeting.recording_enabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Recording wasn't enabled for this meeting.")
    existing = db.scalar(select(MeetingRecording).where(MeetingRecording.meeting_id == meeting_id, MeetingRecording.status == RecordingStatus.RECORDING))
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A recording is already in progress.")

    recording = MeetingRecording(meeting_id=meeting_id, status=RecordingStatus.RECORDING)
    db.add(recording)
    db.add(MeetingMessage(meeting_id=meeting_id, user_id=None, message=f"{actor.full_name} started recording.", is_system=True))
    db.commit()

    ip, ua = _client_meta(request)
    write_audit_event(db, AuthEventType.meeting_recording_started, ip, ua, user=actor, detail=f"Recording started: {meeting.title}", module="meetings", target=meeting.meeting_token, status="success")
    return RecordingOut(id=recording.id, status=recording.status.value, file_path=None, duration_seconds=None, file_size_bytes=None, started_at=recording.started_at, completed_at=None)


@router.post("/{meeting_id}/recordings/{recording_id}/stop", response_model=RecordingOut)
def stop_recording(meeting_id: int, recording_id: int, request: Request, db: Session = Depends(get_db), actor: User = Depends(require_permission("meetings.manage"))):
    """Marks the recording pass as stopped. Without a deployed Jibri
    pipeline (see backend/MEETINGS_DEPLOYMENT.md) no file was actually
    captured, so this honestly reports FAILED rather than pretending a
    file exists — once Jibri is deployed, its completion webhook should
    call the (separate, to-be-added) completion endpoint with the real
    file_path/duration/size before this status is reached."""
    recording = db.get(MeetingRecording, recording_id)
    if recording is None or recording.meeting_id != meeting_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recording not found")
    if recording.status != RecordingStatus.RECORDING:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Recording isn't in progress.")

    recording.status = RecordingStatus.FAILED
    recording.completed_at = datetime.now(timezone.utc)
    db.add(recording)
    meeting = _get_meeting_or_404(db, meeting_id)
    db.add(MeetingMessage(meeting_id=meeting_id, user_id=None, message=f"{actor.full_name} stopped recording. No capture pipeline is deployed yet — see deployment docs.", is_system=True))
    db.commit()

    ip, ua = _client_meta(request)
    write_audit_event(db, AuthEventType.meeting_recording_stopped, ip, ua, user=actor, detail=f"Recording stopped: {meeting.title}", module="meetings", target=meeting.meeting_token, status="success")
    return RecordingOut(id=recording.id, status=recording.status.value, file_path=recording.file_path, duration_seconds=recording.duration_seconds, file_size_bytes=recording.file_size_bytes, started_at=recording.started_at, completed_at=recording.completed_at)


@router.get("/{meeting_id}/recordings/{recording_id}/download")
def download_recording(meeting_id: int, recording_id: int, db: Session = Depends(get_db), _actor: User = Depends(require_permission("meetings.view"))):
    recording = db.get(MeetingRecording, recording_id)
    if recording is None or recording.meeting_id != meeting_id or not recording.file_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recording file not available")
    path = get_recording_path(recording.file_path)
    return FileResponse(path, media_type="video/mp4", filename=f"meeting-{meeting_id}-recording-{recording_id}.mp4")


@router.delete("/{meeting_id}/recordings/{recording_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_meeting_recording(meeting_id: int, recording_id: int, db: Session = Depends(get_db), actor: User = Depends(require_permission("meetings.delete"))):
    recording = db.get(MeetingRecording, recording_id)
    if recording is None or recording.meeting_id != meeting_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recording not found")
    if recording.file_path:
        delete_recording(recording.file_path)
    db.delete(recording)
    db.commit()
    return None
