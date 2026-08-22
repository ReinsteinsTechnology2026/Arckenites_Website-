from datetime import datetime

from pydantic import BaseModel, Field


class CreateRoleRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    permission_keys: list[str] = Field(default_factory=list)


class UpdateRoleRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    permission_keys: list[str] | None = None


class RoleOut(BaseModel):
    id: int
    name: str
    slug: str
    description: str | None
    is_system: bool
    user_count: int
    permission_count: int
    created_at: datetime

    model_config = {"from_attributes": True}


class PermissionOut(BaseModel):
    key: str
    module: str
    action: str
    description: str | None

    model_config = {"from_attributes": True}


class RoleDetailOut(RoleOut):
    granted_permission_keys: list[str]
