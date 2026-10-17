"""Retryable file cleanup after account deletion has committed."""
import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.core import uploads
from app.database import SessionLocal
from app.models.file_cleanup import FileCleanupJob

logger = logging.getLogger(__name__)


def cleanup_path(kind: str, reference: str):
    roots = {"support": uploads.UPLOAD_ROOT, "profile_photo": uploads.PROFILE_PHOTO_ROOT}
    root = roots.get(kind)
    if root is None or not reference:
        raise ValueError("Invalid upload cleanup reference")
    root = root.resolve()
    path = (root / reference).resolve()
    if root not in path.parents:
        raise ValueError("Upload cleanup path outside storage root")
    return path


def process_file_cleanup() -> int:
    """No files are touched until their deletion transaction is durable.

    Failures keep jobs for retry. Unlink is idempotent after a process crash.
    References are checked again so another record's upload is never removed.
    """
    from app.models.support import SupportAttachment
    from app.models.user import User

    with SessionLocal() as db:
        jobs = db.scalars(select(FileCleanupJob).order_by(
            FileCleanupJob.last_attempt_at.asc().nulls_first(), FileCleanupJob.id).limit(100)
                          .with_for_update(skip_locked=True)).all()
        for job in jobs:
            try:
                if job.storage_kind == "support":
                    used = db.scalar(select(SupportAttachment.id).where(
                        SupportAttachment.storage_reference == job.storage_reference).limit(1))
                else:
                    used = db.scalar(select(User.id).where(User.photo_path == job.storage_reference).limit(1))
                if used is None:
                    cleanup_path(job.storage_kind, job.storage_reference).unlink(missing_ok=True)
                db.delete(job)
            except (OSError, ValueError):
                job.last_attempt_at = datetime.now(timezone.utc)
                logger.warning("Upload cleanup pending job_id=%s", job.id)
        db.commit()
        return len(jobs)


async def run_file_cleanup_worker():
    while True:
        try:
            await asyncio.to_thread(process_file_cleanup)
        except Exception as exc:
            logger.error("Upload cleanup worker failed error_type=%s", type(exc).__name__)
        await asyncio.sleep(30)
