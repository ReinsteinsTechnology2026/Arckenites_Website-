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
    """What a student sees about their own access. Connection details
    (hostname/port/username) and the current rotating rdp_password are
    populated ONLY while has_access is true — an expired/revoked student
    polling this endpoint gets none of it, since the fields are simply
    absent rather than stale. rdp_password is intentionally shown here:
    it is a short-lived, auto-rotating secret tied to this one grant, not
    the VM's real Administrator password, and it stops working the
    instant this grant ends (both because the Windows Agent removes RDP
    group membership AND because the password itself is rotated away) —
    the group-membership check is the real access control; the password
    is not what enforcement relies on."""
    has_access: bool
    vm_name: str | None = None
    status: str | None = None
    expires_at: datetime | None = None
    hostname: str | None = None
    rdp_port: int | None = None
    student_rdp_username: str | None = None
    rdp_password: str | None = None


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
    # The VM's configured student account name — always present once a VM
    # has ever had a grant, regardless of status, since the agent needs it
    # both to grant/revoke RDP group membership AND to apply rdp_password
    # below even while status is "none" (rotating the password away).
    student_username: str | None = None
    expires_at: datetime | None = None
    # The password the agent should currently have set on student_username's
    # Windows account — present whenever the server has a current value for
    # this VM, active or not. The agent applies this every poll
    # (idempotent); never logged by the agent.
    rdp_password: str | None = None


class VmAgentEventRequest(BaseModel):
    action: str = Field(min_length=1, max_length=30)
    reason: str | None = Field(default=None, max_length=500)
