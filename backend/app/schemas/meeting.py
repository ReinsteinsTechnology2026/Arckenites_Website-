from datetime import datetime

from pydantic import BaseModel, Field


class CreateMeetingRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    host_id: int
    scheduled_at: datetime
    duration_minutes: int = Field(default=60, ge=5, le=480)
    password: str | None = Field(default=None, min_length=4, max_length=100)
    max_participants: int | None = Field(default=None, ge=1, le=500)

    mic_enabled: bool = True
    camera_enabled: bool = True
    screen_share_enabled: bool = True
    chat_enabled: bool = True
    notes_enabled: bool = True
    participant_notes_enabled: bool = False
    recording_enabled: bool = False
    waiting_room_enabled: bool = False
    require_approval: bool = False

    participant_user_ids: list[int] = Field(default_factory=list)


class UpdateMeetingRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    host_id: int | None = None
    scheduled_at: datetime | None = None
    duration_minutes: int | None = Field(default=None, ge=5, le=480)
    password: str | None = Field(default=None, min_length=4, max_length=100)
    clear_password: bool = False
    max_participants: int | None = None

    mic_enabled: bool | None = None
    camera_enabled: bool | None = None
    screen_share_enabled: bool | None = None
    chat_enabled: bool | None = None
    notes_enabled: bool | None = None
    participant_notes_enabled: bool | None = None
    recording_enabled: bool | None = None
    waiting_room_enabled: bool | None = None
    require_approval: bool | None = None


class MeetingListItemOut(BaseModel):
    id: int
    meeting_token: str
    title: str
    host_name: str
    scheduled_at: datetime
    duration_minutes: int
    status: str
    participant_count: int
    has_password: bool
    recording_enabled: bool


class MeetingStatsOut(BaseModel):
    total_meetings: int
    scheduled_meetings: int
    live_meetings: int
    completed_meetings: int
    total_participants: int


class ParticipantOut(BaseModel):
    id: int
    user_id: int | None
    full_name: str
    role: str
    status: str
    joined_at: datetime | None
    left_at: datetime | None


class MeetingDetailOut(BaseModel):
    id: int
    meeting_token: str
    title: str
    description: str | None
    host_id: int
    host_name: str
    scheduled_at: datetime
    duration_minutes: int
    status: str
    started_at: datetime | None
    ended_at: datetime | None
    has_password: bool
    max_participants: int | None

    mic_enabled: bool
    camera_enabled: bool
    screen_share_enabled: bool
    chat_enabled: bool
    notes_enabled: bool
    participant_notes_enabled: bool
    recording_enabled: bool
    waiting_room_enabled: bool
    require_approval: bool

    participants: list[ParticipantOut]


class AddParticipantRequest(BaseModel):
    user_id: int


class MeetingJoinOut(BaseModel):
    status: str  # READY (join now) or WAITING (in the lobby, not yet admitted)
    meeting_id: int
    meeting_token: str
    title: str
    display_name: str
    is_moderator: bool
    jitsi_domain: str | None = None
    room_name: str | None = None
    mic_enabled: bool = True
    camera_enabled: bool = True
    screen_share_enabled: bool = True
    chat_enabled: bool = True
    notes_enabled: bool = True


class MessageOut(BaseModel):
    id: int
    user_id: int | None
    sender_name: str
    message: str
    is_system: bool
    created_at: datetime


class SendMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class NoteOut(BaseModel):
    content: str
    action_items: list[dict]
    updated_by_name: str | None
    updated_at: datetime


class UpdateNoteRequest(BaseModel):
    content: str = Field(max_length=50000)
    action_items: list[dict] = Field(default_factory=list)


class MyMeetingOut(BaseModel):
    id: int
    meeting_token: str
    title: str
    host_name: str
    scheduled_at: datetime
    duration_minutes: int
    status: str
    my_role: str
    has_password: bool


class RecordingOut(BaseModel):
    id: int
    status: str
    file_path: str | None
    duration_seconds: int | None
    file_size_bytes: int | None
    started_at: datetime
    completed_at: datetime | None
