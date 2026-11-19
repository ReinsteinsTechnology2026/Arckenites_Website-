"""Notices a recording that has silently stopped working while the class goes on.

The media server tells us about most failures by webhook. It cannot tell us
about a crashed or killed Egress container, or a recording that never began,
because nothing is left alive to send the event. Left alone, such a recording
would show "Recording" until the trainer ends the class and the stop request
finally gives up minutes later. This runs from the worker's periodic
reconciliation (every few seconds) and:

* re-queues a start that is waiting for someone to publish media (the start
  itself gives up with a clear reason after a bounded time);
* marks a recording FAILED when the Egress that was started is gone (its room
  participant vanished and stayed away past a grace period), when it reports a
  failure we missed, or when it joined but never began recording in time;
* never touches a class that has ended (the normal stop/finalize path owns it),
  and never changes consent: it only ever moves a recording towards FAILED.

Every decision is made under the run lock after re-reading current state, so it
is safe to run concurrently and repeatedly.
"""
import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.core import job_queue, media_events
from app.core.class_lifecycle import lock_run
from app.core.class_runs import as_utc, utcnow
from app.core.media_server import (EGRESS_FAILED, EGRESS_GONE, EGRESS_MISSING, EGRESS_OK, MediaServerUnavailable,
                                   egress_health, get_media_server, room_name_for_run)
from app.models.class_run import (
    APPROVAL_APPROVED,
    CAPTURE_RECORDING,
    CAPTURE_STARTING,
    REC_APPROVED,
    REC_STARTING,
    RUN_OPEN_STATES,
    ClassRecording,
    ClassSessionRun,
    RecordingEvent,
)

logger = logging.getLogger("recording_watchdog")
RESUME_BUCKET_SECONDS = 15
EVENT_EGRESS_MISSING = "egress_missing"
EVENT_EGRESS_RECOVERED = "egress_recovered"

LOST_DETAIL = "The recording process stopped responding, so the recording was stopped."
NEVER_BEGAN_DETAIL = "The recorder joined the class but never began recording."


def _last_event(db: Session, recording_id) -> RecordingEvent | None:
    return db.scalar(select(RecordingEvent).where(RecordingEvent.recording_id == recording_id)
                     .order_by(RecordingEvent.id.desc()).limit(1))


def check_recordings(db: Session, now: datetime | None = None) -> dict[str, int]:
    """One sweep. Returns counters for logs and tests. Commits per recording."""
    now = now or utcnow()
    counts = {"checked": 0, "resumed": 0, "failed": 0}
    candidates = db.execute(
        select(ClassRecording.id).join(ClassSessionRun, ClassSessionRun.id == ClassRecording.run_id).where(
            ClassRecording.capture_state.in_((CAPTURE_STARTING, CAPTURE_RECORDING)),
            ClassSessionRun.state.in_(RUN_OPEN_STATES))).scalars().all()
    db.rollback()
    for recording_id in candidates:
        try:
            outcome = _check_one(db, recording_id, now)
        except Exception:  # noqa: BLE001 - one bad recording must not stop the sweep
            db.rollback()
            logger.exception("recording check failed recording_id=%s", recording_id)
            continue
        counts["checked"] += 1
        if outcome in counts:
            counts[outcome] += 1
    return counts


def _check_one(db: Session, recording_id, now: datetime) -> str | None:
    recording = db.get(ClassRecording, recording_id)
    run = db.get(ClassSessionRun, recording.run_id) if recording is not None else None
    if recording is None or run is None or run.state not in RUN_OPEN_STATES:
        db.rollback()
        return None
    if recording.egress_id is None:
        return _resume_waiting_start(db, run, recording, now)

    egress_id, room = recording.egress_id, room_name_for_run(run.id)
    try:
        state, code = egress_health(get_media_server(), room, egress_id)
    except MediaServerUnavailable:
        db.rollback()
        return None   # can't tell right now; try again next sweep
    started_at = _started_waiting_since(db, recording)
    age = (now - started_at).total_seconds()

    if state == EGRESS_OK:
        last = _last_event(db, recording.id)
        if last is not None and last.event == EVENT_EGRESS_MISSING:
            db.add(RecordingEvent(run_id=run.id, recording_id=recording.id, event=EVENT_EGRESS_RECOVERED))
            db.commit()
        if recording.capture_state == CAPTURE_STARTING and age >= settings.recording_activation_timeout_seconds:
            return _fail(db, run.id, recording.id, egress_id, "start_timeout", NEVER_BEGAN_DETAIL, now, retry=False)
        db.rollback()
        return None
    if state == EGRESS_FAILED:
        return _fail(db, run.id, recording.id, egress_id, code or "egress_failed",
                     "The media server reported a recording failure.", now, retry=True)
    if state == EGRESS_GONE:
        return _fail(db, run.id, recording.id, egress_id, "egress_lost", LOST_DETAIL, now, retry=True)
    if state == EGRESS_MISSING:
        last = _last_event(db, recording.id)
        if last is None or last.event != EVENT_EGRESS_MISSING:
            db.add(RecordingEvent(run_id=run.id, recording_id=recording.id, event=EVENT_EGRESS_MISSING))
            db.commit()
            return None
        gone_for = (now - as_utc(last.created_at)).total_seconds()
        if gone_for >= settings.recording_egress_lost_grace_seconds:
            return _fail(db, run.id, recording.id, egress_id, "egress_lost", LOST_DETAIL, now, retry=True)
    db.rollback()
    return None


def _started_waiting_since(db: Session, recording: ClassRecording) -> datetime:
    accepted = db.scalar(select(RecordingEvent.created_at).where(
        RecordingEvent.recording_id == recording.id, RecordingEvent.event == "recording_start_accepted")
        .order_by(RecordingEvent.id.desc()).limit(1))
    return as_utc(accepted or recording.created_at)


def _resume_waiting_start(db: Session, run: ClassSessionRun, recording: ClassRecording, now: datetime) -> str | None:
    """A start that is waiting for media (or was interrupted between phases) is
    picked up again; the start job decides whether to begin or give up."""
    if (recording.capture_state != CAPTURE_STARTING or run.approval_status != APPROVAL_APPROVED
            or run.recording_state not in (REC_STARTING, REC_APPROVED)):
        db.rollback()
        return None
    bucket = int(now.timestamp() // RESUME_BUCKET_SECONDS)
    queued = job_queue.enqueue(db, "recording.start", f"recording.start:{run.id}:resume:{bucket}", {"run_id": run.id}, run_at=now)
    db.commit()
    return "resumed" if queued else None


def _fail(db: Session, run_id: int, recording_id, egress_id: str, code: str, detail: str, now: datetime,
          *, retry: bool) -> str | None:
    run = lock_run(db, run_id)
    recording = db.get(ClassRecording, recording_id)
    if (run is None or recording is None or recording.egress_id != egress_id
            or recording.capture_state not in (CAPTURE_STARTING, CAPTURE_RECORDING)
            or run.state not in RUN_OPEN_STATES):
        db.rollback()
        return None   # something else already resolved it
    media_events.fail_attempt(db, run, recording, code, detail, now, retry=retry)
    db.commit()
    # Best effort: ask the media server to drop the dead recording so a later
    # attempt does not adopt it. Failure here changes nothing we have recorded.
    try:
        get_media_server().stop_recording(egress_id)
    except Exception:  # noqa: BLE001
        logger.info("could not stop failed recording egress_id=%s", egress_id)
    return "failed"
