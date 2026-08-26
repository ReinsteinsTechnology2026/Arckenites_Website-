from app.schemas.profile import PublicProfileOut
from pydantic import BaseModel


class BatchMembersOut(BaseModel):
    """Who's in this batch, as seen by a fellow student or the trainer —
    public identity only, never username/email/phone/ids."""
    trainer: PublicProfileOut | None
    students: list[PublicProfileOut]
