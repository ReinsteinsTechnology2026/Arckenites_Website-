"""Getting a verified recording to its final storage and into Class Videos.

Storage and publication are separate steps with separate retries:

  STORED (verified, on local staging)
    -> upload to the company OneDrive and verify the remote copy   [recording.upload]
    -> create the Class Videos entry                               [recording.publish]

A recording is published ONLY after its remote copy is verified. If publishing
fails, only publishing is retried; the class is never re-recorded and the
file is never re-uploaded unnecessarily. The Class Videos entry is unique per
recording, so retries can never create a duplicate.

The one exception is a clearly-labelled DEVELOPMENT mode (see
`dev_publish_from_staging_allowed`) used to exercise playback before OneDrive
access exists. It is off by default and impossible in production.
"""
import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.core import job_queue
from app.core.class_lifecycle import lock_run
from app.core.class_runs import as_utc, utcnow
from app.core.job_queue import PermanentFailure, RetryLater
from app.core.onedrive_store import OneDriveError, OneDriveNotReady, OneDriveStore
from app.core.recording_storage import StagingStore
from app.core.scheduling import business_tz
from app.crud.audit import write_audit_event
from app.models.audit_log import AuthEventType
from app.models.batch import Batch
from app.models.batch_resources import ClassVideo
from app.models.class_run import (
    CAPTURE_STORED,
    PUBLICATION_FAILED,
    PUBLICATION_NOT_PUBLISHED,
    PUBLICATION_PUBLISHED,
    PUBLICATION_PUBLISHING,
    REC_PUBLISHED,
    ClassRecording,
    ClassSessionRun,
    RecordingEvent,
)
from app.models.class_session import ClassSession

logger = logging.getLogger("recording_publish")
LOCAL_PROVIDER = StagingStore.provider


def dev_publish_from_staging_allowed() -> bool:
    return settings.recording_publish_from_staging and settings.env.strip().lower() in ("development", "test")


def _event(db: Session, rec: ClassRecording, event: str, detail: str | None = None) -> None:
    db.add(RecordingEvent(run_id=rec.run_id, recording_id=rec.id, event=event, detail=detail[:500] if detail else None))


def remote_name(rec: ClassRecording) -> str:
    return f"{rec.id}.mp4"  # unique, server-generated, no user input


def schedule_after_stored(db: Session, rec: ClassRecording) -> None:
    """Called when a recording has just been verified. Decides the next step
    without ever claiming success it has not earned."""
    ready, reason = OneDriveStore().readiness()
    if ready:
        job_queue.enqueue(db, "recording.upload", f"recording.upload:{rec.id}", {"recording_id": str(rec.id)})
        _event(db, rec, "upload_queued")
    elif dev_publish_from_staging_allowed():
        rec.storage_provider = LOCAL_PROVIDER
        job_queue.enqueue(db, "recording.publish", f"recording.publish:{rec.id}", {"recording_id": str(rec.id)})
        _event(db, rec, "dev_publish_from_staging_queued", "development mode: not stored in OneDrive")
    else:
        _event(db, rec, "awaiting_remote_storage", reason)  # stays verified on local staging, unpublished


def requeue_unpublished(db: Session) -> int:
    """Self-healing, called from the periodic reconcile: a verified recording
    that is waiting for OneDrive is queued the moment OneDrive becomes ready."""
    ready, _ = OneDriveStore().readiness()
    if not ready:
        return 0
    waiting = db.scalars(select(ClassRecording).where(
        ClassRecording.capture_state == CAPTURE_STORED, ClassRecording.remote_verified_at.is_(None),
        ClassRecording.publication_state == PUBLICATION_NOT_PUBLISHED, ClassRecording.deleted_at.is_(None))).all()
    for rec in waiting:
        job_queue.enqueue(db, "recording.upload", f"recording.upload:{rec.id}", {"recording_id": str(rec.id)})
    return len(waiting)


def upload_recording(db: Session, recording_id: str | UUID) -> str:
    rid = UUID(str(recording_id))
    rec = db.get(ClassRecording, rid, with_for_update=True)
    if rec is None or rec.capture_state != CAPTURE_STORED:
        db.rollback()
        return "not_ready"
    if rec.remote_verified_at is not None:
        job_queue.enqueue(db, "recording.publish", f"recording.publish:{rec.id}", {"recording_id": str(rec.id)})
        db.commit()
        return "already_uploaded"

    store = OneDriveStore()
    ready, reason = store.readiness()
    if not ready:
        db.rollback()
        raise RetryLater(reason or "OneDrive isn't ready", delay_seconds=300)
    staging = StagingStore()
    if not staging.exists_nonempty(rec.staging_key or ""):
        rec.publication_state = PUBLICATION_FAILED
        rec.error_code, rec.error_detail = "staging_file_missing", "The verified local copy is missing."
        _event(db, rec, "upload_failed", "staging file missing")
        db.commit()
        raise PermanentFailure("the verified staging file is missing")

    rec.publication_state = PUBLICATION_PUBLISHING
    db.commit()
    path = staging.path_for(rec.staging_key)
    try:
        remote = store.upload_file(path, remote_name(rec))  # verified size + hash, or raises
    except OneDriveNotReady as exc:
        raise RetryLater(str(exc), delay_seconds=300) from exc
    except OneDriveError as exc:
        raise RetryLater(str(exc), delay_seconds=60) from exc

    rec = db.get(ClassRecording, rid, with_for_update=True)
    rec.storage_provider = OneDriveStore.provider
    rec.remote_item_id = remote.item_id
    rec.remote_verified_at = utcnow()
    _event(db, rec, "uploaded_and_verified", f"{remote.size} bytes")
    job_queue.enqueue(db, "recording.publish", f"recording.publish:{rec.id}", {"recording_id": str(rec.id)})
    db.commit()
    return "uploaded"


def _title(db: Session, rec: ClassRecording, run: ClassSessionRun, batch: Batch) -> str:
    session = db.get(ClassSession, run.class_session_id) if run.class_session_id else None
    day = as_utc(run.scheduled_start_at).astimezone(business_tz()).strftime("%d %b %Y")
    return (f"{session.title} - {day}" if session is not None else f"Class on {day}")[:200]


def publish_recording(db: Session, recording_id: str | UUID) -> str:
    """Creates the Class Videos entry. Idempotent: safe to run any number of times."""
    rid = UUID(str(recording_id))
    rec = db.get(ClassRecording, rid, with_for_update=True)
    if rec is None:
        db.rollback()
        return "no_such_recording"
    if rec.publication_state == PUBLICATION_PUBLISHED:
        db.rollback()
        return "already_published"
    if rec.capture_state != CAPTURE_STORED or rec.verified_at is None or rec.deleted_at is not None:
        db.rollback()
        return "not_publishable"
    verified_remote = rec.remote_verified_at is not None and rec.storage_provider == OneDriveStore.provider
    verified_dev = rec.storage_provider == LOCAL_PROVIDER and dev_publish_from_staging_allowed()
    if not (verified_remote or verified_dev):
        db.rollback()
        return "no_verified_final_copy"  # never published on the strength of a local file alone

    run = lock_run(db, rec.run_id)
    batch = db.get(Batch, rec.batch_id)
    if db.scalar(select(ClassVideo.id).where(ClassVideo.recording_id == rec.id)) is None:
        try:
            with db.begin_nested():
                db.add(ClassVideo(batch_id=rec.batch_id, title=_title(db, rec, run, batch), source="RECORDING",
                                  recording_id=rec.id, video_url=None))
        except IntegrityError:
            pass  # a concurrent publish created it: exactly what we want
    rec.publication_state = PUBLICATION_PUBLISHED
    rec.published_at = utcnow()
    run.recording_state = REC_PUBLISHED
    _event(db, rec, "published", rec.storage_provider)
    write_audit_event(db, AuthEventType.recording_published, None, None, detail="Recording published to Class Videos",
                      module="recordings", target=f"run:{rec.run_id}", status="success", commit=False)
    db.commit()
    return "published"


# --- dead-letter handlers ------------------------------------------------------

def _publication_failed(db: Session, recording_id: str, job) -> None:
    rec = db.get(ClassRecording, UUID(recording_id), with_for_update=True)
    if rec is None or rec.publication_state == PUBLICATION_PUBLISHED:
        return
    rec.publication_state = PUBLICATION_FAILED
    rec.error_code, rec.error_detail = "publication_failed", (job.last_error or "publication failed")[:500]
    _event(db, rec, "publication_failed", job.last_error)
    write_audit_event(db, AuthEventType.recording_failed, None, None, detail="Recording publication failed",
                      module="recordings", target=f"run:{rec.run_id}", status="failure", commit=False)
    logger.error("recording publication failed run_id=%s", rec.run_id)


def on_dead(db: Session, job) -> None:
    import json
    _publication_failed(db, json.loads(job.payload)["recording_id"], job)
