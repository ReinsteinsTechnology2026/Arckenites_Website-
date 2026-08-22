from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.models.support import TicketCategoryEnum, TicketPriorityEnum, TicketStatusEnum


def _validate_choice(value: str, enum_cls, field_name: str) -> str:
    if value not in enum_cls._value2member_map_:
        allowed = ", ".join(e.value for e in enum_cls)
        raise ValueError(f"{field_name} must be one of: {allowed}")
    return value


class CreateTicketRequest(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    category: str
    priority: str = TicketPriorityEnum.medium.value
    description: str = Field(min_length=1)

    @field_validator("category")
    @classmethod
    def validate_category(cls, value: str) -> str:
        return _validate_choice(value, TicketCategoryEnum, "category")

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, value: str) -> str:
        return _validate_choice(value, TicketPriorityEnum, "priority")


class SendMessageRequest(BaseModel):
    message: str = Field(min_length=1)


class ChangeStatusRequest(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        return _validate_choice(value, TicketStatusEnum, "status")


class ChangePriorityRequest(BaseModel):
    priority: str

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, value: str) -> str:
        return _validate_choice(value, TicketPriorityEnum, "priority")


class AssignTicketRequest(BaseModel):
    assigned_to: int | None = None


class AttachmentOut(BaseModel):
    id: int
    file_name: str
    file_type: str
    file_size: int
    created_at: datetime


class TicketMessageOut(BaseModel):
    id: int
    sender_id: int
    sender_name: str
    sender_type: str
    message: str
    is_internal: bool
    created_at: datetime
    attachments: list[AttachmentOut] = []


class TicketListItemOut(BaseModel):
    id: int
    ticket_number: str
    requester_id: int
    requester_name: str
    requester_role: str
    subject: str
    category: str
    priority: str
    status: str
    assigned_to: int | None
    assigned_to_name: str | None
    created_at: datetime
    updated_at: datetime
    unread: bool


class TicketDetailOut(BaseModel):
    id: int
    ticket_number: str
    requester_id: int
    requester_name: str
    requester_username: str
    requester_role: str
    subject: str
    category: str
    priority: str
    status: str
    assigned_to: int | None
    assigned_to_name: str | None
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None
    closed_at: datetime | None
    messages: list[TicketMessageOut]


class TicketStatsOut(BaseModel):
    total: int
    open: int
    in_progress: int
    waiting_for_student: int
    resolved: int
    unread: int
