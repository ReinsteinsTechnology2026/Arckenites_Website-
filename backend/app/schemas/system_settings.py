from datetime import datetime

from pydantic import BaseModel, Field, field_validator

_ALLOWED_DATE_FORMATS = {"DD MMM YYYY", "MM/DD/YYYY", "DD/MM/YYYY", "YYYY-MM-DD"}


class UpdateSystemSettingsRequest(BaseModel):
    institute_name: str | None = Field(default=None, min_length=1, max_length=200)
    logo_url: str | None = Field(default=None, max_length=500)
    contact_email: str | None = Field(default=None, max_length=255)
    timezone: str | None = Field(default=None, min_length=1, max_length=100)
    date_format: str | None = None

    session_timeout_minutes: int | None = Field(default=None, ge=5, le=1440)
    max_login_attempts: int | None = Field(default=None, ge=3, le=10)
    lockout_duration_minutes: int | None = Field(default=None, ge=5, le=1440)
    require_strong_passwords: bool | None = None

    notify_new_account: bool | None = None
    notify_password_reset: bool | None = None
    notify_security_alert: bool | None = None

    maintenance_mode: bool | None = None

    @field_validator("date_format")
    @classmethod
    def validate_date_format(cls, value: str | None) -> str | None:
        if value is not None and value not in _ALLOWED_DATE_FORMATS:
            raise ValueError(f"date_format must be one of: {', '.join(sorted(_ALLOWED_DATE_FORMATS))}")
        return value


class SystemSettingsOut(BaseModel):
    institute_name: str
    logo_url: str | None
    contact_email: str | None
    timezone: str
    date_format: str

    session_timeout_minutes: int
    max_login_attempts: int
    lockout_duration_minutes: int
    require_strong_passwords: bool

    notify_new_account: bool
    notify_password_reset: bool
    notify_security_alert: bool

    maintenance_mode: bool

    updated_at: datetime

    model_config = {"from_attributes": True}
