"""Binds job kinds to the lifecycle and recording code. Importing this module
registers every handler with the queue."""
import json
import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import class_lifecycle as lifecycle
from app.core import job_queue, recording_finalize, recording_pipeline, recording_publish, recording_watchdog
from app.core.class_runs import utcnow
from app.core.job_queue import RetryLater, register
from app.core.media_server import (MediaServerNotConfigured, MediaServerUnavailable, get_media_server,
                                   room_name_for_run)
from app.core.notify import notify_batch_update
from app.models.batch import Batch, BatchEnrollment
from app.models.class_run import RUN_OPEN_STATES, BackgroundJob, ClassSessionRun
from app.models.user import User

logger = logging.getLogger("job_handlers")


def _payload(job: BackgroundJob) -> dict:
    return json.loads(job.payload or "{}")


@register("session.activate")
def _activate(db: Session, job: BackgroundJob) -> None:
    lifecycle.activate_run(db, _payload(job)["run_id"])


@register("run.notify_open")
def _notify_open(db: Session, job: BackgroundJob) -> None:
    """Tells the trainer and the batch's students the class is available.
    Best effort per recipient: a mail problem never fails or repeats the job."""
    run = db.get(ClassSessionRun, _payload(job)["run_id"])
    if run is None or run.state not in RUN_OPEN_STATES:
        return
    batch = db.get(Batch, run.batch_id)
    if batch is None:
        return
    message = "Your class is open - you can join now."
    recipients: list[User] = [e.student for e in db.scalars(
        select(BatchEnrollment).where(BatchEnrollment.batch_id == batch.id)).all() if e.student is not None]
    if batch.trainer is not None:
        recipients.append(batch.trainer)
    for person in recipients:
        try:
            notify_batch_update(person, batch.name, message)
        except Exception:  # noqa: BLE001
            logger.warning("class-open notification failed run_id=%s", run.id)


@register("run.scheduled_end")
def _scheduled_end(db: Session, job: BackgroundJob) -> None:
    data = _payload(job)
    lifecycle.scheduled_end(db, data["run_id"], datetime.fromisoformat(data["end_at"]))


@register("run.grace_expiry")
def _grace_expiry(db: Session, job: BackgroundJob) -> None:
    data = _payload(job)
    lifecycle.grace_expired(db, data["run_id"], datetime.fromisoformat(data["left_at"]))


@register("room.close")
def _room_close(db: Session, job: BackgroundJob) -> None:
    """Removes everyone from the media room when the class has ended."""
    run_id = _payload(job)["run_id"]
    try:
        get_media_server().delete_room(room_name_for_run(run_id))
    except MediaServerNotConfigured:
        logger.info("room.close skipped: no media server configured run_id=%s", run_id)
    except MediaServerUnavailable as exc:
        raise RetryLater(str(exc), delay_seconds=20) from exc


@register("recording.start", on_dead=recording_pipeline.on_start_dead)
def _recording_start(db: Session, job: BackgroundJob) -> None:
    recording_pipeline.start_recording(db, _payload(job)["run_id"])


@register("recording.stop", on_dead=recording_pipeline.on_stop_dead)
def _recording_stop(db: Session, job: BackgroundJob) -> None:
    recording_pipeline.stop_recording(db, _payload(job)["run_id"])


@register("recording.finalize", on_dead=recording_finalize.on_finalize_dead)
def _recording_finalize(db: Session, job: BackgroundJob) -> None:
    recording_finalize.finalize_recording(db, _payload(job)["recording_id"])


@register("recording.upload", on_dead=recording_publish.on_dead)
def _recording_upload(db: Session, job: BackgroundJob) -> None:
    recording_publish.upload_recording(db, _payload(job)["recording_id"])


@register("recording.publish", on_dead=recording_publish.on_dead)
def _recording_publish(db: Session, job: BackgroundJob) -> None:
    recording_publish.publish_recording(db, _payload(job)["recording_id"])


@register("sessions.reconcile")
def _reconcile(db: Session, job: BackgroundJob) -> None:
    lifecycle.reconcile_sessions(db)
    recording_publish.requeue_unpublished(db)
    # Independent of the schedule work above: a problem checking recordings
    # must never stop classes from opening and closing.
    try:
        recording_watchdog.check_recordings(db)
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("recording watchdog sweep failed")
