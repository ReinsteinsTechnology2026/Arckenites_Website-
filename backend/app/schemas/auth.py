from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=128)


class AdminRoleSummary(BaseModel):
    id: int
    name: str
    slug: str

    model_config = {"from_attributes": True}


class UserOut(BaseModel):
    id: int
    username: str
    full_name: str
    role: str
    must_change_password: bool
    program: str | None = None
    profile_completed: bool = True
    # Populated explicitly by the route (not bare from_attributes) since it
    # needs a fresh permissions-table query — see crud/permissions.py.
    admin_role: AdminRoleSummary | None = None
    permissions: list[str] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class MeResponse(UserOut):
    pass


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)
