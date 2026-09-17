from datetime import datetime

from pydantic import BaseModel, Field


class CreateClassVideoRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    video_url: str = Field(min_length=1, max_length=500)
    description: str | None = None


class ClassVideoOut(BaseModel):
    id: int
    batch_id: int
    batch_name: str
    title: str
    video_url: str
    description: str | None
    created_at: datetime


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
