from datetime import datetime

from pydantic import BaseModel, Field


class CreateLabVmRequest(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    hostname: str = Field(min_length=1, max_length=255)
    rdp_port: int = Field(default=3389, ge=1, le=65535)
    operating_system: str | None = Field(default=None, max_length=100)
    description: str | None = None
    student_rdp_username: str = Field(min_length=1, max_length=150)


class UpdateLabVmRequest(BaseModel):
    """All fields optional — partial updates, PATCH-style, same convention
    as the rest of the admin API. Does not touch the agent token — that's
    regenerate-token's job specifically, kept separate so an unrelated edit
    can never accidentally invalidate an already-configured agent."""
    name: str | None = Field(default=None, min_length=1, max_length=150)
    hostname: str | None = Field(default=None, min_length=1, max_length=255)
    rdp_port: int | None = Field(default=None, ge=1, le=65535)
    operating_system: str | None = None
    description: str | None = None
    student_rdp_username: str | None = Field(default=None, min_length=1, max_length=150)
    is_active: bool | None = None


class LabVmOut(BaseModel):
    id: int
    name: str
    hostname: str
    rdp_port: int
    operating_system: str | None
    description: str | None
    student_rdp_username: str
    is_active: bool
    agent_status: str  # "online" | "offline" | "never_connected" — computed, not stored
    agent_last_seen_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class LabVmCreatedOut(LabVmOut):
    """Returned ONLY from the create-VM response — the one and only time
    the plaintext agent token is ever visible anywhere. Never returned by
    any GET/list endpoint afterward."""
    agent_token: str


class GrantLabVmAccessRequest(BaseModel):
    vm_id: int
    duration_minutes: int = Field(default=120, ge=1, le=1440)


class RevokeLabVmAccessRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class LabVmAccessOut(BaseModel):
    id: int
    student_id: int
    student_name: str
    vm_id: int
    vm_name: str
    status: str
    granted_at: datetime
    expires_at: datetime
    granted_by_name: str | None
    revoked_at: datetime | None
    revoked_by_name: str | None


class MyLabVmAccessOut(BaseModel):
    """What a student sees about their own access — deliberately no
    hostname/port/username fields; those only appear once status is
    active, via the separate connect-file endpoint, never in this status
    payload (so an expired/revoked student can't harvest connection
    details just by polling this endpoint)."""
    has_access: bool
    vm_name: str | None = None
    status: str | None = None
    expires_at: datetime | None = None


class LabVmAuditEntryOut(BaseModel):
    id: int
    student_id: int
    student_name: str
    vm_id: int | None
    vm_name: str | None
    action: str
    performed_by_name: str | None
    reason: str | None
    created_at: datetime


class VmAgentHeartbeatResponse(BaseModel):
    status: str  # "active" | "none"
    student_username: str | None = None
    expires_at: datetime | None = None


class VmAgentEventRequest(BaseModel):
    action: str = Field(min_length=1, max_length=30)
    reason: str | None = Field(default=None, max_length=500)
