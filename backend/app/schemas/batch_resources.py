from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator


class CreateClassVideoRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    video_url: str = Field(min_length=1, max_length=500)
    description: str | None = None

    @field_validator("video_url")
    @classmethod
    def _http_links_only(cls, value: str) -> str:
        # A javascript:/data: link rendered as an href would run script in a
        # student's session. Only web links may be posted.
        value = value.strip()
        if not value.lower().startswith(("https://", "http://")):
            raise ValueError("The link must start with http:// or https://")
        return value


class ClassVideoOut(BaseModel):
    id: int
    batch_id: int
    batch_name: str
    title: str
    video_url: str | None
    description: str | None
    created_at: datetime
    # LINK = a posted URL; RECORDING = an Arckenites class recording, played via
    # the authorized stream (it never has a URL).
    source: str = "LINK"
    recording_id: str | None = None
    duration_seconds: int | None = None
    session_date: date | None = None
    trainer_name: str | None = None


class CreateStudyMaterialRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    file_url: str = Field(min_length=1, max_length=500)
    description: str | None = None


class StudyMaterialOut(BaseModel):
    id: int
    batch_id: int
    batch_name: str
    title: str
    file_url: str
    description: str | None
    created_at: datetime
