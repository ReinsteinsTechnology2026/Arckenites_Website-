import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.deps import get_current_user
from app.core.meetings import get_participant_row, meeting_member_user_ids, require_member, room_name_for
from app.core.security import verify_password
from app.core.ws_manager import manager
from app.database import get_db
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
from app.models.user import User
from app.schemas.meeting import (
    MeetingJoinOut,
    MessageOut,
    MyMeetingOut,
    NoteOut,
    ParticipantOut,
    RecordingOut,
    SendMessageRequest,
    UpdateNoteRequest,
)

router = APIRouter(prefix="/meetings", tags=["meetings"])


def _get_meeting_by_token_or_404(db: Session, token: str) -> Meeting:
    meeting = db.scalar(select(Meeting).where(Meeting.meeting_token == token))
    if meeting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invalid or expired meeting link.")
    return meeting


@router.get("/me", response_model=list[MyMeetingOut])
def my_meetings(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Every meeting this user hosts or is invited to — live first, then
    soonest-scheduled, then past. This is the only way a student/staff
    member discovers a meeting exists at all, short of being handed the
    direct link, so the dashboard "Meetings" panel depends on this."""
    hosted_ids = set(db.scalars(select(Meeting.id).where(Meeting.host_id == user.id)).all())
    participant_meeting_ids = set(db.scalars(
        select(MeetingParticipant.meeting_id).where(
            MeetingParticipant.user_id == user.id,
            MeetingParticipant.status.notin_([ParticipantStatus.REJECTED, ParticipantStatus.REMOVED]),
        )
    ).all())
    meeting_ids = hosted_ids | participant_meeting_ids
    if not meeting_ids:
        return []

    meetings = db.scalars(select(Meeting).where(Meeting.id.in_(meeting_ids))).all()
    host_ids = {m.host_id for m in meetings}
    hosts = {u.id: u.full_name for u in db.scalars(select(User).where(User.id.in_(host_ids))).all()} if host_ids else {}

    status_priority = {"LIVE": 0, "SCHEDULED": 1, "COMPLETED": 2, "CANCELLED": 3}
    meetings.sort(key=lambda m: (status_priority.get(m.status.value, 9), m.scheduled_at))

    return [
        MyMeetingOut(
            id=m.id, meeting_token=m.meeting_token, title=m.title, host_name=hosts.get(m.host_id, "Unknown"),
            scheduled_at=m.scheduled_at, duration_minutes=m.duration_minutes, status=m.status.value,
            my_role="HOST" if m.host_id == user.id else "PARTICIPANT", has_password=m.password_hash is not None,
        )
        for m in meetings
    ]


class JoinRequest(BaseModel):
    password: str | None = None


@router.post("/{token}/join", response_model=MeetingJoinOut)
def join_meeting(
    token: str, payload: JoinRequest,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    meeting = _get_meeting_by_token_or_404(db, token)

    if meeting.status == MeetingStatus.CANCELLED:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This meeting was cancelled.")
    if meeting.status == MeetingStatus.COMPLETED:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This meeting has already ended.")

    participant, is_moderator = require_member(db, meeting, user)
    # require_member intentionally returns None for the host (they don't
    # need a participant row to have access) — but the host DOES get one at
    # meeting creation, and it should track joined/left like everyone else's.
    if participant is None and is_moderator:
        participant = get_participant_row(db, meeting.id, user.id)

    if meeting.status == MeetingStatus.SCHEDULED:
        if not is_moderator:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This meeting hasn't started yet. Please wait for the host to start it.")
        # The host joining is what starts the meeting for everyone else.
        meeting.status = MeetingStatus.LIVE
        meeting.started_at = datetime.now(timezone.utc)
        db.add(meeting)
        db.add(MeetingMessage(meeting_id=meeting.id, user_id=None, message=f"{user.full_name} started the meeting.", is_system=True))
        db.commit()

    if meeting.password_hash and not (payload.password and verify_password(payload.password, meeting.password_hash)):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect meeting password.")

    if meeting.max_participants is not None and not is_moderator:
        joined = db.scalars(select(MeetingParticipant).where(
            MeetingParticipant.meeting_id == meeting.id, MeetingParticipant.status == ParticipantStatus.JOINED
        )).all()
        if len(joined) >= meeting.max_participants:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This meeting is full.")

    waiting = (
        meeting.waiting_room_enabled
        and not is_moderator
        and participant is not None
        and participant.status not in (ParticipantStatus.ADMITTED, ParticipantStatus.JOINED)
    )

    if participant is not None:
        participant.status = ParticipantStatus.WAITING if waiting else ParticipantStatus.JOINED
        if not waiting and participant.joined_at is None:
            participant.joined_at = datetime.now(timezone.utc)
        db.add(participant)
        db.commit()

    if waiting:
        return MeetingJoinOut(
            status="WAITING", meeting_id=meeting.id, meeting_token=meeting.meeting_token,
            title=meeting.title, display_name=user.full_name, is_moderator=False,
        )

    return MeetingJoinOut(
        status="READY", meeting_id=meeting.id, meeting_token=meeting.meeting_token, title=meeting.title,
        display_name=user.full_name, is_moderator=is_moderator,
        jitsi_domain=settings.meet_domain, room_name=room_name_for(meeting),
        mic_enabled=meeting.mic_enabled, camera_enabled=meeting.camera_enabled,
        screen_share_enabled=meeting.screen_share_enabled, chat_enabled=meeting.chat_enabled,
        notes_enabled=meeting.notes_enabled,
    )


@router.get("/{meeting_id}/participants", response_model=list[ParticipantOut])
def get_meeting_participants(meeting_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    meeting = db.get(Meeting, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
    require_member(db, meeting, user)

    rows = db.scalars(select(MeetingParticipant).where(MeetingParticipant.meeting_id == meeting_id)).all()
    user_ids = {p.user_id for p in rows if p.user_id is not None}
    names = {u.id: u.full_name for u in db.scalars(select(User).where(User.id.in_(user_ids))).all()} if user_ids else {}
    return [
        ParticipantOut(id=p.id, user_id=p.user_id, full_name=names.get(p.user_id, "Unknown"), role=p.role.value, status=p.status.value, joined_at=p.joined_at, left_at=p.left_at)
        for p in rows
    ]


@router.get("/{meeting_id}/waiting", response_model=list[ParticipantOut])
def get_waiting_participants(meeting_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Polled by the host's meeting room UI to show who's in the lobby."""
    meeting = db.get(Meeting, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
    _participant, is_moderator = require_member(db, meeting, user)
    if not is_moderator:
        return []  # non-hosts simply see no one waiting, rather than a 403 on a polled endpoint

    rows = db.scalars(select(MeetingParticipant).where(
        MeetingParticipant.meeting_id == meeting_id, MeetingParticipant.status == ParticipantStatus.WAITING
    )).all()
    user_ids = {p.user_id for p in rows if p.user_id is not None}
    names = {u.id: u.full_name for u in db.scalars(select(User).where(User.id.in_(user_ids))).all()} if user_ids else {}
    return [
        ParticipantOut(id=p.id, user_id=p.user_id, full_name=names.get(p.user_id, "Unknown"), role=p.role.value, status=p.status.value, joined_at=p.joined_at, left_at=p.left_at)
        for p in rows
    ]


@router.post("/{meeting_id}/participants/{participant_id}/admit", status_code=status.HTTP_204_NO_CONTENT)
def admit_participant(meeting_id: int, participant_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    meeting = db.get(Meeting, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
    _require_moderator(db, meeting, user)
    participant = db.get(MeetingParticipant, participant_id)
    if participant is None or participant.meeting_id != meeting_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Participant not found")
    participant.status = ParticipantStatus.ADMITTED
    db.add(participant)
    db.commit()
    return None


@router.post("/{meeting_id}/participants/{participant_id}/reject", status_code=status.HTTP_204_NO_CONTENT)
def reject_participant(meeting_id: int, participant_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    meeting = db.get(Meeting, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
    _require_moderator(db, meeting, user)
    participant = db.get(MeetingParticipant, participant_id)
    if participant is None or participant.meeting_id != meeting_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Participant not found")
    participant.status = ParticipantStatus.REJECTED
    db.add(participant)
    db.commit()
    return None


@router.post("/{meeting_id}/participants/{participant_id}/remove", status_code=status.HTTP_204_NO_CONTENT)
def host_remove_participant(meeting_id: int, participant_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Kick a currently-joined participant — they can't rejoin (REMOVED is
    treated the same as REJECTED by require_member's block on rejoin)."""
    meeting = db.get(Meeting, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
    _require_moderator(db, meeting, user)
    participant = db.get(MeetingParticipant, participant_id)
    if participant is None or participant.meeting_id != meeting_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Participant not found")
    if participant.role == ParticipantRole.HOST:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Can't remove the host.")
    participant.status = ParticipantStatus.REMOVED
    participant.left_at = datetime.now(timezone.utc)
    db.add(participant)
    db.commit()
    return None


@router.post("/{meeting_id}/leave", status_code=status.HTTP_204_NO_CONTENT)
def leave_meeting(meeting_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    meeting = db.get(Meeting, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
    participant, is_moderator = require_member(db, meeting, user)
    if participant is None and is_moderator:
        participant = get_participant_row(db, meeting.id, user.id)
    if participant is not None:
        participant.status = ParticipantStatus.LEFT
        participant.left_at = datetime.now(timezone.utc)
        db.add(participant)
        db.commit()
    return None


@router.get("/{meeting_id}/messages", response_model=list[MessageOut])
def get_messages(
    meeting_id: int, after_id: int = 0,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    meeting = db.get(Meeting, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
    require_member(db, meeting, user)

    rows = db.scalars(
        select(MeetingMessage)
        .where(MeetingMessage.meeting_id == meeting_id, MeetingMessage.id > after_id)
        .order_by(MeetingMessage.created_at)
        .limit(200)
    ).all()
    user_ids = {m.user_id for m in rows if m.user_id is not None}
    names = {u.id: u.full_name for u in db.scalars(select(User).where(User.id.in_(user_ids))).all()} if user_ids else {}
    return [
        MessageOut(id=m.id, user_id=m.user_id, sender_name=names.get(m.user_id, "System"), message=m.message, is_system=m.is_system, created_at=m.created_at)
        for m in rows
    ]


@router.post("/{meeting_id}/messages", response_model=MessageOut, status_code=status.HTTP_201_CREATED)
async def send_message(
    meeting_id: int, payload: SendMessageRequest,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    meeting = db.get(Meeting, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
    require_member(db, meeting, user)
    if not meeting.chat_enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Chat is disabled for this meeting.")

    entry = MeetingMessage(meeting_id=meeting_id, user_id=user.id, message=payload.message, is_system=False)
    db.add(entry)
    db.commit()
    db.refresh(entry)

    out = MessageOut(id=entry.id, user_id=user.id, sender_name=user.full_name, message=entry.message, is_system=False, created_at=entry.created_at)
    for uid in meeting_member_user_ids(db, meeting):
        if uid != user.id:
            await manager.send_to_user(uid, {"type": "meeting_message", "meeting_id": meeting_id, "message": out.model_dump(mode="json")})
    return out


@router.get("/{meeting_id}/notes", response_model=NoteOut)
def get_notes(meeting_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    meeting = db.get(Meeting, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
    require_member(db, meeting, user)
    if not meeting.notes_enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Notes are disabled for this meeting.")

    note = db.scalar(select(MeetingNote).where(MeetingNote.meeting_id == meeting_id))
    if note is None:
        return NoteOut(content="", action_items=[], updated_by_name=None, updated_at=datetime.now(timezone.utc))
    author = db.get(User, note.updated_by) if note.updated_by else None
    return NoteOut(content=note.content, action_items=json.loads(note.action_items or "[]"), updated_by_name=author.full_name if author else None, updated_at=note.updated_at)


@router.patch("/{meeting_id}/notes", response_model=NoteOut)
def update_notes(
    meeting_id: int, payload: UpdateNoteRequest,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    meeting = db.get(Meeting, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
    _participant, is_moderator = require_member(db, meeting, user)
    if not meeting.notes_enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Notes are disabled for this meeting.")
    if not is_moderator and not meeting.participant_notes_enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the host can edit notes for this meeting.")

    note = db.scalar(select(MeetingNote).where(MeetingNote.meeting_id == meeting_id))
    if note is None:
        note = MeetingNote(meeting_id=meeting_id)
    note.content = payload.content
    note.action_items = json.dumps(payload.action_items)
    note.updated_by = user.id
    db.add(note)
    db.commit()
    db.refresh(note)

    return NoteOut(content=note.content, action_items=json.loads(note.action_items or "[]"), updated_by_name=user.full_name, updated_at=note.updated_at)


# ---------------------------------------------------------------------------
# Self-service host controls — a meeting's host isn't necessarily an admin
# (staff can host too), so these mirror the equivalent /admin/meetings/*
# actions but authorize on "are you this meeting's moderator" rather than
# the global admin permission system. The meeting room UI calls these;
# the admin dashboard calls the permission-gated /admin/meetings/* ones.
# ---------------------------------------------------------------------------

def _require_moderator(db: Session, meeting: Meeting, user: User) -> None:
    _participant, is_moderator = require_member(db, meeting, user)
    if not is_moderator:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the host can do that.")


@router.post("/{meeting_id}/start", status_code=status.HTTP_204_NO_CONTENT)
def host_start_meeting(meeting_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    meeting = db.get(Meeting, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
    _require_moderator(db, meeting, user)
    if meeting.status != MeetingStatus.SCHEDULED:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Meeting is {meeting.status.value.lower()}, not scheduled.")
    meeting.status = MeetingStatus.LIVE
    meeting.started_at = datetime.now(timezone.utc)
    db.add(meeting)
    db.add(MeetingMessage(meeting_id=meeting.id, user_id=None, message=f"{user.full_name} started the meeting.", is_system=True))
    db.commit()
    return None


@router.post("/{meeting_id}/end", status_code=status.HTTP_204_NO_CONTENT)
def host_end_meeting(meeting_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    meeting = db.get(Meeting, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
    _require_moderator(db, meeting, user)
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
        rec.status = RecordingStatus.FAILED
        rec.completed_at = now
        db.add(rec)
    db.add(MeetingMessage(meeting_id=meeting.id, user_id=None, message=f"{user.full_name} ended the meeting.", is_system=True))
    db.commit()
    return None


@router.post("/{meeting_id}/recordings/start", response_model=RecordingOut, status_code=status.HTTP_201_CREATED)
def host_start_recording(meeting_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    meeting = db.get(Meeting, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
    _require_moderator(db, meeting, user)
    if meeting.status != MeetingStatus.LIVE:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Meeting isn't live.")
    if not meeting.recording_enabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Recording wasn't enabled for this meeting.")
    existing = db.scalar(select(MeetingRecording).where(MeetingRecording.meeting_id == meeting_id, MeetingRecording.status == RecordingStatus.RECORDING))
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A recording is already in progress.")

    recording = MeetingRecording(meeting_id=meeting_id, status=RecordingStatus.RECORDING)
    db.add(recording)
    db.add(MeetingMessage(meeting_id=meeting_id, user_id=None, message=f"{user.full_name} started recording.", is_system=True))
    db.commit()
    return RecordingOut(id=recording.id, status=recording.status.value, file_path=None, duration_seconds=None, file_size_bytes=None, started_at=recording.started_at, completed_at=None)


@router.post("/{meeting_id}/recordings/{recording_id}/stop", response_model=RecordingOut)
def host_stop_recording(meeting_id: int, recording_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    meeting = db.get(Meeting, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
    _require_moderator(db, meeting, user)

    recording = db.get(MeetingRecording, recording_id)
    if recording is None or recording.meeting_id != meeting_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recording not found")
    if recording.status != RecordingStatus.RECORDING:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Recording isn't in progress.")

    recording.status = RecordingStatus.FAILED  # honest default until a real Jibri pipeline posts a completion
    recording.completed_at = datetime.now(timezone.utc)
    db.add(recording)
    db.add(MeetingMessage(meeting_id=meeting_id, user_id=None, message=f"{user.full_name} stopped recording.", is_system=True))
    db.commit()
    return RecordingOut(id=recording.id, status=recording.status.value, file_path=recording.file_path, duration_seconds=recording.duration_seconds, file_size_bytes=recording.file_size_bytes, started_at=recording.started_at, completed_at=recording.completed_at)
