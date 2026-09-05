import enum
import secrets
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MeetingStatus(str, enum.Enum):
    SCHEDULED = "SCHEDULED"
    LIVE = "LIVE"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class ParticipantRole(str, enum.Enum):
    HOST = "HOST"
    CO_HOST = "CO_HOST"
    PARTICIPANT = "PARTICIPANT"


class ParticipantStatus(str, enum.Enum):
    INVITED = "INVITED"
    WAITING = "WAITING"
    ADMITTED = "ADMITTED"
    JOINED = "JOINED"
    LEFT = "LEFT"
    REMOVED = "REMOVED"
    REJECTED = "REJECTED"


class RecordingStatus(str, enum.Enum):
    RECORDING = "RECORDING"
    PROCESSING = "PROCESSING"
    AVAILABLE = "AVAILABLE"
    FAILED = "FAILED"


def _generate_meeting_token() -> str:
    return secrets.token_urlsafe(8)


class Meeting(Base):
    """A scheduled/live/past meeting. `meeting_token` is the public,
    unguessable id used in join links and as the Jitsi room name — never
    expose the numeric `id` in a URL."""

    __tablename__ = "meetings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    meeting_token: Mapped[str] = mapped_column(String(32), unique=True, index=True, default=_generate_meeting_token)

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    host_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)

    password_hash: Mapped[str | None] = mapped_column(String(200), nullable=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    max_participants: Mapped[int | None] = mapped_column(Integer, nullable=True)

    status: Mapped[MeetingStatus] = mapped_column(
        Enum(MeetingStatus, name="meeting_status_enum"), nullable=False, default=MeetingStatus.SCHEDULED
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Feature toggles the host sets at creation — enforced both by the
    # meeting-room client (Jitsi config overrides) and server-side on the
    # relevant endpoints (e.g. chat_enabled gates POST /meetings/{id}/chat).
    mic_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    camera_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    screen_share_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    chat_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notes_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    participant_notes_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    recording_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    waiting_room_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    require_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MeetingParticipant(Base):
    """One row per person invited/attending a meeting. `user_id` is nullable
    to leave room for guest-by-email participants later without a schema
    change, though the current API only invites registered users."""

    __tablename__ = "meeting_participants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    meeting_id: Mapped[int] = mapped_column(ForeignKey("meetings.id"), nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)

    role: Mapped[ParticipantRole] = mapped_column(
        Enum(ParticipantRole, name="meeting_participant_role_enum"), nullable=False, default=ParticipantRole.PARTICIPANT
    )
    status: Mapped[ParticipantStatus] = mapped_column(
        Enum(ParticipantStatus, name="meeting_participant_status_enum"), nullable=False, default=ParticipantStatus.INVITED
    )

    joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MeetingMessage(Base):
    """Persisted chat history for a meeting — delivered live over the
    existing per-user WebSocket fanned out to every current participant,
    and reloaded from here on join / on the meeting detail page."""

    __tablename__ = "meeting_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    meeting_id: Mapped[int] = mapped_column(ForeignKey("meetings.id"), nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    message: Mapped[str] = mapped_column(Text, nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MeetingNote(Base):
    """One shared, collaboratively-edited notes document per meeting
    (not per-user) — the host can always edit; participants only when the
    meeting's participant_notes_enabled flag is on. action_items is a
    simple JSON-encoded [{text, done}] list, kept as text to avoid a JSON
    column type dependency."""

    __tablename__ = "meeting_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    meeting_id: Mapped[int] = mapped_column(ForeignKey("meetings.id"), nullable=False, unique=True, index=True)

    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    action_items: Mapped[str] = mapped_column(Text, nullable=False, default="[]")

    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MeetingRecording(Base):
    """A recording pass for a meeting. `file_path` is a storage-relative
    path (served the same way as other uploads — see app/core/uploads.py),
    never a raw blob in the database. Populated by the Jibri integration
    once deployed; see backend/MEETINGS_DEPLOYMENT.md."""

    __tablename__ = "meeting_recordings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    meeting_id: Mapped[int] = mapped_column(ForeignKey("meetings.id"), nullable=False, index=True)

    status: Mapped[RecordingStatus] = mapped_column(
        Enum(RecordingStatus, name="meeting_recording_status_enum"), nullable=False, default=RecordingStatus.RECORDING
    )
    file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
