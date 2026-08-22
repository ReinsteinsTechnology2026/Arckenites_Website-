import uuid
from datetime import datetime

from pydantic import BaseModel


class SessionOut(BaseModel):
    id: uuid.UUID
    device_label: str
    ip_address: str | None
    issued_at: datetime
    is_current: bool
