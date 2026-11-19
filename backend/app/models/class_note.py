from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ClassPrivateNote(Base):
    """One student's private notes for one class occurrence.

    Deliberately separate from `meeting_notes` (a shared, meeting-bound
    document). The owner is ALWAYS the authenticated student: no endpoint takes
    a user id, so there is no way to address another student's row. Trainers and
    admins have no read path at all. Rows are owned by the student account and
    are deleted with it.
    """

    __tablename__ = "class_private_notes"
    __table_args__ = (UniqueConstraint("run_id", "user_id", name="uq_class_private_notes_run_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("class_session_runs.id"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
