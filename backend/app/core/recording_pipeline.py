"""Starting and stopping the server-side recording for an occurrence.

Scope of this stage: the START and STOP control path and its safety rules.
Finalization, integrity verification, storage and publication follow in the
LiveKit/Egress stage and plug into the states defined here.

Hard rules:

* A recording is only ever started for an occurrence whose approval is
  persisted as APPROVED by the class trainer, while the class is open. A
  forged, stale or duplicate job for an unapproved, declined, ended or
  cancelled occurrence starts nothing.
* Exactly one recording per occurrence. The row is saved BEFORE the media
  server is asked to start, and a retry first looks for an already-running
  recording to adopt, so a crash between "started" and "saved" can never
  produce a second recording.
* The media server is the source of truth. This module records what it asked
  for (STARTING); RECORDING is only set from the media server's own events.
"""
import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.core import class_runs, job_queue, media_events
from app.core.class_runs import as_utc, utcnow
from app.core.class_lifecycle import lock_run
from app.core.job_queue import PermanentFailure, RetryLater
from app.core.media_server import (MediaServerNotConfigured, MediaServerUnavailable, RecordingRefused,
                                   get_media_server, require_class_room, room_has_published_media,
                                   room_name_for_run)
from app.core.recording_storage import new_staging_key
from app.crud.audit import write_audit_event
from app.models.audit_log import AuthEventType
from app.models.class_run import (
    APPROVAL_APPROVED,
    APPROVER_TRAINER,
    CAPTURE_FAILED,
    CAPTURE_FINALIZING,
    CAPTURE_RECORDING,
    CAPTURE_STARTING,
    REC_APPROVED,
    REC_FAILED,
    REC_NOT_RECORDED,
    REC_STARTING,
    REC_STOPPING,
    RUN_OPEN_STATES,
    ClassRecording,
    ClassSessionRun,
    RecordingEvent,
)

logger = logging.getLogger("recording_pipeline")
ACTIVE_CAPTURE = (CAPTURE_STARTING, CAPTURE_RECORDING)
RETRY_DELAY_SECONDS = 20


def _active_recording(db: Session, run_id: int) -> ClassRecording | None:
    return db.scalar(select(ClassRecording).where(
        ClassRecording.run_id == run_id, ClassRecording.capture_state != CAPTURE_FAILED))


def _event(db: Session, run: ClassSessionRun, event: str, recording: ClassRecording | None = None,
           detail: str | None = None) -> None:
    db.add(RecordingEvent(run_id=run.id, recording_id=recording.id if recording else None,
                          event=event, detail=detail[:500] if detail else None))


EVENT_WAITING_FOR_MEDIA = class_runs.EVENT_WAITING_FOR_MEDIA
NO_MEDIA_DETAIL = ("Nobody was publishing camera, microphone or screen share, so there was nothing to record. "
                   "Turn one on and ask for the recording again.")


def _wait_for_media(db: Session, run_id: int, recording_id) -> str:
    """Egress cannot start on an empty stage: it would sit in STARTING and then
    abort. So the start waits (bounded) for someone to publish media, tells the
    trainer it is waiting, and gives up with a clear reason when time is up.
    The periodic watchdog re-queues this job while it waits."""
    now = utcnow()
    run = lock_run(db, run_id)
    recording = db.get(ClassRecording, recording_id)
    if run is None or recording is None or recording.capture_state != CAPTURE_STARTING or recording.egress_id:
        db.rollback()
        return "no_longer_waiting"
    waited = (now - as_utc(recording.created_at)).total_seconds()
    if waited >= settings.recording_start_wait_seconds:
        media_events.fail_attempt(db, run, recording, "no_media_published", NO_MEDIA_DETAIL, now, retry=False)
        db.commit()
        return "gave_up_no_media"
    already = db.scalar(select(RecordingEvent.id).where(
        RecordingEvent.recording_id == recording.id, RecordingEvent.event == EVENT_WAITING_FOR_MEDIA))
    if already is None:
        _event(db, run, EVENT_WAITING_FOR_MEDIA, recording)
    db.commit()
    return "waiting_for_media"


def start_recording(db: Session, run_id: int) -> str:
    """Job handler body for `recording.start`. Commits its own phases (the
    external call must never sit inside an uncommitted transaction). Returns a
    short outcome string, useful in logs and tests."""
    run = lock_run(db, run_id)
    if run is None:
        return "no_such_run"
    # --- the approval gate: nothing below runs without it -----------------
    # Approved AND approved by the class trainer: nothing else can authorize a recording.
    if run.approval_status != APPROVAL_APPROVED or run.approval_source != APPROVER_TRAINER:
        db.rollback()
        return "not_approved"
    if run.state not in RUN_OPEN_STATES:
        if run.recording_state == REC_APPROVED:
            run.recording_state = REC_NOT_RECORDED
            _event(db, run, "start_skipped_class_not_open")
        db.commit()
        return "class_not_open"

    recording = _active_recording(db, run.id)
    if recording is not None and recording.egress_id:
        db.rollback()
        return "already_started"
    failed_egress_ids = set(db.scalars(select(ClassRecording.egress_id).where(
        ClassRecording.run_id == run.id, ClassRecording.capture_state == CAPTURE_FAILED,
        ClassRecording.egress_id.is_not(None))).all())

    unavailable = class_runs.recording_unavailable_reason()
    if unavailable is not None:
        db.rollback()
        raise RetryLater(unavailable, delay_seconds=RETRY_DELAY_SECONDS)

    # --- phase 1: persist the intent, then release the lock ----------------
    is_new = recording is None
    if recording is None:
        recording = ClassRecording(
            run_id=run.id, batch_id=run.batch_id, class_session_id=run.class_session_id,
            attempt_no=(db.scalar(select(ClassRecording.attempt_no).where(ClassRecording.run_id == run.id)
                                  .order_by(ClassRecording.attempt_no.desc())) or 0) + 1,
            capture_state=CAPTURE_STARTING, staging_key=new_staging_key(),
        )
        db.add(recording)
    run.recording_state = REC_STARTING
    if is_new:
        _event(db, run, "recording_start_requested", recording)   # once per attempt, not per wait-poll
    db.commit()
    recording_id, staging_key, room = recording.id, recording.staging_key, room_name_for_run(run.id)

    # --- phase 2: ask the media server (adopt a running recording if any) ---
    try:
        require_class_room(room)  # defence in depth: only a batch-class room is ever recorded
        media = get_media_server()
        if room_has_published_media(media, room) is False:
            return _wait_for_media(db, run_id, recording_id)
        active = media.find_active_recording(room)
        if active in failed_egress_ids:
            active = None   # a dead attempt's leftover is not something to adopt
        egress_id = active or media.start_room_recording(room, staging_key)
    except RecordingRefused as exc:
        raise PermanentFailure(str(exc)) from exc
    except MediaServerNotConfigured as exc:
        raise PermanentFailure(str(exc)) from exc
    except MediaServerUnavailable as exc:
        raise RetryLater(str(exc), delay_seconds=RETRY_DELAY_SECONDS) from exc

    # --- phase 3: remember what we started -----------------------------------
    run = lock_run(db, run_id)
    recording = db.get(ClassRecording, recording_id)
    recording.egress_id = egress_id
    _event(db, run, "recording_start_accepted", recording)
    db.commit()
    if run.state not in RUN_OPEN_STATES:
        # The class ended while we were starting. Stop what we just started.
        stop_recording(db, run_id)
        return "started_then_stopped"
    return "started"


def stop_recording(db: Session, run_id: int) -> str:
    """Job handler body for `recording.stop`: stop capture and begin
    finalization. Idempotent."""
    run = lock_run(db, run_id)
    if run is None:
        return "no_such_run"
    recording = _active_recording(db, run.id)

    if recording is None:
        if run.recording_state in (REC_APPROVED, REC_STARTING, REC_STOPPING):
            run.recording_state = REC_NOT_RECORDED
            _event(db, run, "stop_nothing_to_stop")
        db.commit()
        return "nothing_to_stop"

    if recording.capture_state not in ACTIVE_CAPTURE:
        db.commit()
        return "already_stopping"

    if not recording.egress_id:
        # We recorded the intent but never reached the media server.
        recording.capture_state = CAPTURE_FAILED
        recording.error_code, recording.error_detail = "never_started", "The class ended before recording started."
        run.recording_state = REC_NOT_RECORDED
        _event(db, run, "stop_never_started", recording)
        db.commit()
        return "never_started"

    egress_id = recording.egress_id
    try:
        get_media_server().stop_recording(egress_id)
    except MediaServerNotConfigured as exc:
        db.rollback()
        raise PermanentFailure(str(exc)) from exc
    except MediaServerUnavailable as exc:
        db.rollback()
        raise RetryLater(str(exc), delay_seconds=RETRY_DELAY_SECONDS) from exc

    recording.capture_state = CAPTURE_FINALIZING
    recording.ended_at = recording.ended_at or utcnow()
    run.recording_state = REC_STOPPING
    _event(db, run, "recording_stop_requested", recording)
    db.commit()
    return "stopped"


# --- dead-letter handling: tell the truth and alert -------------------------

def _fail_recording(db: Session, run_id: int, code: str, detail: str) -> None:
    run = lock_run(db, run_id)
    if run is None:
        return
    recording = _active_recording(db, run.id)
    if recording is not None:
        recording.capture_state = CAPTURE_FAILED
        recording.error_code, recording.error_detail = code, detail[:500]
    run.recording_state = REC_FAILED
    _event(db, run, "recording_failed", recording, detail)
    write_audit_event(db, AuthEventType.recording_failed, None, None, detail=f"Recording failed ({code})",
                      module="recordings", target=f"run:{run.id}", status="failure", commit=False)
    # Operators are alerted through the error log and the audit trail
    # (Activity Logs) until admin notification delivery is wired in.
    logger.error("recording failed run_id=%s code=%s", run.id, code)


def on_start_dead(db: Session, job) -> None:
    _fail_recording(db, _run_id(job), "start_failed", job.last_error or "recording could not be started")


def on_stop_dead(db: Session, job) -> None:
    _fail_recording(db, _run_id(job), "stop_failed", job.last_error or "recording could not be stopped")


def _run_id(job) -> int:
    import json
    return int(json.loads(job.payload)["run_id"])
