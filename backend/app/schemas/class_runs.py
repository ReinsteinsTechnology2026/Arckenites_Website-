from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class RecordingApprovalRequest(BaseModel):
    decision: Literal["approve", "decline"]
    # The wording version the user was actually shown. The server rejects an
    # approval made against stale wording.
    consent_version: str | None = Field(default=None, max_length=32)


class ExtendRequest(BaseModel):
    minutes: int = Field(ge=1, le=240)


class JoinOut(BaseModel):
    run_id: int
    batch_id: int
    run_state: str
    livekit_url: str
    # Short-lived, room-scoped. Never log or store it.
    token: str
    room_name: str
    identity: str
    display_name: str
    is_trainer: bool
    expires_in: int
    recording: "RunRecordingStatusOut"


class PromptOut(BaseModel):
    show: bool
    consent_version: str
    consent_text: str


class RunRecordingStatusOut(BaseModel):
    run_id: int
    batch_id: int
    run_state: str
    recording_state: str
    approval_status: str
    approval_source: str | None
    approval_decided_at: datetime | None
    # True only for the batch's assigned trainer (never an admin). Drives whether
    # the room shows the Start Recording control; the server re-checks it on
    # every action regardless.
    can_control: bool
    # True only while recording is verifiably active. Students see this and
    # nothing else about the recording pipeline.
    is_recording: bool
    consent_version: str
    consent_text: str
    # Trainer only (always None/False for everyone else): why recording has not
    # started or has stopped, in plain words, and whether "Retry recording" is
    # available. Students never see anything about the recording pipeline.
    notice_code: str | None = None
    notice: str | None = None
    can_retry: bool = False


JoinOut.model_rebuild()
