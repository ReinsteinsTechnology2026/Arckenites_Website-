from datetime import datetime

from pydantic import BaseModel


class RoleCount(BaseModel):
    role: str
    count: int


class DashboardStatsResponse(BaseModel):
    total_users: int
    users_by_role: list[RoleCount]
    active_users: int
    inactive_users: int
    locked_accounts: int
    new_users_last_7_days: int
    logins_today: int
    failed_logins_today: int
    generated_at: datetime


class ActivityEntry(BaseModel):
    id: int
    event_type: str
    actor_name: str
    message: str
    created_at: datetime


class ActivityResponse(BaseModel):
    items: list[ActivityEntry]
