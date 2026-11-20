"""Shared reading of a batch's Class Videos (posted links + published recordings)."""
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.core.class_runs import as_utc
from app.core.scheduling import business_tz
from app.models.batch import Batch
from app.models.batch_resources import ClassVideo
from app.models.class_run import PUBLICATION_PUBLISHED, ClassRecording, ClassSessionRun
from app.models.user import User
from app.schemas.batch_resources import ClassVideoOut


def visible_videos_select():
    """Class Videos entries that may be shown: every posted link, plus a
    recording only while it is PUBLISHED (never revoked, never deleted)."""
    return (
        select(ClassVideo)
        .outerjoin(ClassRecording, ClassRecording.id == ClassVideo.recording_id)
        .where(or_(
            ClassVideo.recording_id.is_(None),
            and_(ClassRecording.publication_state == PUBLICATION_PUBLISHED, ClassRecording.deleted_at.is_(None)),
        ))
    )


def video_out(db: Session, row: ClassVideo, batch_name: str) -> ClassVideoOut:
    out = dict(id=row.id, batch_id=row.batch_id, batch_name=batch_name, title=row.title,
               video_url=row.video_url, description=row.description, created_at=row.created_at,
               source=row.source)
    if row.recording_id is not None:
        recording = db.get(ClassRecording, row.recording_id)
        run = db.get(ClassSessionRun, recording.run_id) if recording is not None else None
        batch = db.get(Batch, row.batch_id)
        trainer = db.get(User, batch.trainer_id) if batch is not None and batch.trainer_id else None
        out.update(
            recording_id=str(row.recording_id),
            duration_seconds=recording.duration_seconds if recording is not None else None,
            session_date=as_utc(run.scheduled_start_at).astimezone(business_tz()).date() if run is not None else None,
            trainer_name=trainer.full_name if trainer is not None else None,
        )
    return ClassVideoOut(**out)
