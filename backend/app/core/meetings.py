from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.meeting import Meeting, MeetingParticipant, ParticipantRole, ParticipantStatus
from app.models.user import User


def room_name_for(meeting: Meeting) -> str:
    """The Jitsi room name — derived from the meeting's own unguessable
    token, so anyone who can reach the room already had a valid invite;
    no separate secret needs to be generated or stored."""
    return f"ArckMeeting-{meeting.meeting_token}"


def get_participant_row(db: Session, meeting_id: int, user_id: int) -> MeetingParticipant | None:
    return db.scalar(
        select(MeetingParticipant).where(
            MeetingParticipant.meeting_id == meeting_id, MeetingParticipant.user_id == user_id
        )
    )


def require_member(db: Session, meeting: Meeting, user: User) -> tuple[MeetingParticipant | None, bool]:
    """Raises 403 if `user` has no standing to be in this meeting at all
    (not the host, not on the invite list). Returns (participant_row,
    is_host) — participant_row is None only for the host, who doesn't need
    one to have access. This is the server-side membership boundary every
    meeting-scoped endpoint (join, chat, notes, recordings) must call —
    the frontend never gets to decide who belongs in a meeting."""
    if meeting.host_id == user.id:
        return None, True

    participant = get_participant_row(db, meeting.id, user.id)
    if participant is None or participant.status in (ParticipantStatus.REJECTED, ParticipantStatus.REMOVED):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You're not invited to this meeting.")
    return participant, participant.role in (ParticipantRole.HOST, ParticipantRole.CO_HOST)


def meeting_member_user_ids(db: Session, meeting: Meeting) -> list[int]:
    """Every user who currently belongs to this meeting — used to fan out
    chat messages over the existing per-user WebSocket connections."""
    ids = set(
        db.scalars(select(MeetingParticipant.user_id).where(MeetingParticipant.meeting_id == meeting.id)).all()
    )
    ids.discard(None)
    ids.add(meeting.host_id)
    return list(ids)
