"""Private Microsoft-storage objects. Messages and access metadata stay in SQL."""
import uuid
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Integer, String, Uuid, func, Index
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base

class PortalFile(Base):
    __tablename__ = 'portal_files'
    __table_args__ = (Index('ix_portal_files_scope', 'scope', 'target_id', 'state'),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    target_id: Mapped[int] = mapped_column(Integer, nullable=False)
    uploader_id: Mapped[int | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    drive_id: Mapped[str] = mapped_column(String(300), nullable=False)
    folder: Mapped[str] = mapped_column(String(300), nullable=False)
    remote_name: Mapped[str] = mapped_column(String(100), nullable=False)
    remote_item_id: Mapped[str | None] = mapped_column(String(300), nullable=True)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[str] = mapped_column(String(150), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default='PENDING')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
