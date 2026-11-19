"""Applies authenticated media-server events to class occurrences and recordings.

Called by the webhook route AFTER the signature has been verified and the event
id recorded in the same transaction. Nothing here trusts the browser: presence
and recording state come only from the media server.

Ordering rules (events can repeat and arrive out of order):

* Trainer presence is decided by the event's own timestamp; stale events are
  ignored, and a same-second tie never leaves the trainer marked absent.
* A 'left' event is NOT enough to start the termination clock by itself: a
  tab refresh produces a DUPLICATE_IDENTITY departure for the old connection,
  and we also ask the media server whether the trainer is really gone.
* Recording transitions only move forward (STARTING -> RECORDING ->
  FINALIZING ...). A late or repeated 'active' after 'complete' is a no-op.
"""
import json
import logging
import re
from datetime import datetime, timezone

from livekit import api
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.core import class_lifecycle as lifecycle
from app.core import job_queue
from app.core.class_runs import as_utc, utcnow
from app.core.media_server import (MediaServerUnavailable, get_media_server, room_name_for_run,
                                   run_id_from_room_name)
from app.crud.audit import write_audit_event
from app.models.audit_log import AuthEventType
from app.models.batch import Batch
from app.models.class_run import (
    APPROVAL_APPROVED,
    CAPTURE_FAILED,
    CAPTURE_FINALIZING,
    CAPTURE_RECORDING,
    CAPTURE_STARTING,
    REC_APPROVED,
    REC_FAILED,
    REC_PROCESSING,
    REC_RECORDING,
    REC_STARTING,
    RUN_OPEN_STATES,
    ClassRecording,
    ClassSessionRun,
    RecordingEvent,
)

logger = logging.getLogger("media_events")
_IDENTITY_RE = re.compile(r"^u(\d{1,12})$")
FAILED_STATUSES = {api.EgressStatus.EGRESS_FAILED, api.EgressStatus.EGRESS_ABORTED, api.EgressStatus.EGRESS_LIMIT_REACHED}
DUPLICATE_IDENTITY = api.DisconnectReason.DUPLICATE_IDENTITY


def identity_for_user(user_id: int) -> str:
    return f"u{user_id}"


def user_id_from_identity(identity: str) -> int | None:
    match = _IDENTITY_RE.match(identity or "")
    return int(match.group(1)) if match else None


def _event_time(event) -> datetime | None:
    return datetime.fromtimestamp(event.created_at, timezone.utc) if event.created_at else None


def handle_event(db: Session, event, now: datetime | None = None) -> str:
    """Returns a short outcome string for logs/tests. Does not commit."""
    now = now or utcnow()
    kind = event.event
    if kind in ("participant_joined", "participant_left"):
        return _presence(db, event, kind, now)
    if kind.startswith("egress_"):
        return _egress(db, event, now)
    return "ignored_event"


# --- presence ---------------------------------------------------------------

def _presence(db: Session, event, kind: str, now: datetime) -> str:
    run_id = run_id_from_room_name(event.room.name)
    if run_id is None:
        return "ignored_unknown_room"
    run = db.get(ClassSessionRun, run_id)
    batch = db.get(Batch, run.batch_id) if run is not None else None
    if run is None or batch is None:
        return "ignored_unknown_run"

    identity = event.participant.identity
    if batch.trainer_id is None or identity != identity_for_user(batch.trainer_id):
        return "ignored_not_trainer"  # student presence/attendance is handled separately

    at = _event_time(event)
    if at is not None and (now - at).total_seconds() > settings.media_webhook_presence_max_age_seconds:
        return "ignored_stale"

    if kind == "participant_joined":
        return "trainer_connected" if lifecycle.mark_trainer_connected(db, run_id, event_at=at, now=now) else "ignored_stale"

    if event.participant.disconnect_reason == DUPLICATE_IDENTITY:
        return "ignored_duplicate_identity"  # the old tab of a refresh; the new connection is live
    try:
        still_there = get_media_server().is_participant_present(room_name_for_run(run_id), identity)
    except MediaServerUnavailable:
        still_there = None  # can't tell: fall back on the grace period, which is the safety net
    if still_there:
        return "ignored_still_present"
    return "trainer_disconnected" if lifecycle.mark_trainer_disconnected(db, run_id, event_at=at, now=now) else "ignored_stale"


# --- recording --------------------------------------------------------------

def _egress(db: Session, event, now: datetime) -> str:
    info = event.egress_info
    if not info.egress_id:
        return "ignored_no_egress_id"
    recording = db.scalar(select(ClassRecording).where(ClassRecording.egress_id == info.egress_id).with_for_update())
    if recording is None:
        return "ignored_unknown_egress"  # not one we started
    run = lifecycle.lock_run(db, recording.run_id)
    if run is None:
        return "ignored_unknown_run"

    status = info.status
    if status == api.EgressStatus.EGRESS_ACTIVE:
        return _egress_active(db, run, recording, now)
    if status == api.EgressStatus.EGRESS_COMPLETE:
        return _egress_complete(db, run, recording, info, now)
    if status in FAILED_STATUSES:
        return _egress_failed(db, run, recording, info, status, now)
    return "ignored_status"  # STARTING / ENDING: nothing to record yet


def _log(db: Session, run: ClassSessionRun, recording: ClassRecording, event: str, detail: str | None = None) -> None:
    db.add(RecordingEvent(run_id=run.id, recording_id=recording.id, event=event, detail=detail[:500] if detail else None))


def _egress_active(db: Session, run: ClassSessionRun, recording: ClassRecording, now: datetime) -> str:
    if recording.capture_state != CAPTURE_STARTING:
        return "ignored_not_forward"
    recording.capture_state = CAPTURE_RECORDING
    recording.started_at = recording.started_at or now
    if run.recording_state in (REC_STARTING, REC_APPROVED):
        run.recording_state = REC_RECORDING  # the first time the UI may say "recording"
    _log(db, run, recording, "recording_started")
    write_audit_event(db, AuthEventType.recording_started, None, None, detail="Recording started",
                      module="recordings", target=f"run:{run.id}", status="success", commit=False)
    return "recording_active"


def _egress_complete(db: Session, run: ClassSessionRun, recording: ClassRecording, info, now: datetime) -> str:
    if recording.capture_state not in (CAPTURE_STARTING, CAPTURE_RECORDING, CAPTURE_FINALIZING):
        return "ignored_not_forward"
    if recording.media_info:
        return "ignored_not_forward"  # completion was already processed (our stop request also sets FINALIZING)
    files = list(info.file_results)
    if not files:
        return _fail(db, run, recording, "no_file_reported", "The media server finished without reporting a file.", now)
    reported = files[0]
    recording.capture_state = CAPTURE_FINALIZING
    recording.ended_at = recording.ended_at or now
    # What the media server SAYS it wrote. These are hints only; the file
    # itself is verified independently before anything is marked stored.
    recording.media_info = json.dumps({"reported": {"size": reported.size, "duration_ns": reported.duration}})
    run.recording_state = REC_PROCESSING
    _log(db, run, recording, "recording_finished_by_media_server")
    job_queue.enqueue(db, "recording.finalize", f"recording.finalize:{recording.id}",
                      {"recording_id": str(recording.id)}, run_at=now)
    return "recording_complete"


def _egress_failed(db: Session, run: ClassSessionRun, recording: ClassRecording, info, status, now: datetime) -> str:
    if recording.capture_state == CAPTURE_FAILED:
        return "ignored_not_forward"
    code = api.EgressStatus.Name(status).removeprefix("EGRESS_").lower()
    return _fail(db, run, recording, f"egress_{code}", info.error or "The media server reported a recording failure.", now)


def fail_attempt(db: Session, run: ClassSessionRun, recording: ClassRecording, code: str, detail: str,
                 now: datetime, *, retry: bool = True) -> str:
    """Marks one recording attempt failed (never deleting it) and, if wanted,
    queues the single automatic retry. `retry=False` is for failures a retry
    cannot fix by itself (e.g. nobody was publishing media): the trainer is
    told and can ask again."""
    return _fail(db, run, recording, code, detail, now, retry=retry)


def _fail(db: Session, run: ClassSessionRun, recording: ClassRecording, code: str, detail: str, now: datetime,
          *, retry: bool = True) -> str:
    recording.capture_state = CAPTURE_FAILED
    recording.error_code, recording.error_detail = code[:64], detail[:500]
    recording.ended_at = recording.ended_at or now
    _log(db, run, recording, "recording_failed", code)
    write_audit_event(db, AuthEventType.recording_failed, None, None, detail=f"Recording failed ({code})",
                      module="recordings", target=f"run:{run.id}", status="failure", commit=False)
    logger.error("recording failed run_id=%s code=%s", run.id, code)

    retry_wanted = (retry and run.state in RUN_OPEN_STATES and run.approval_status == APPROVAL_APPROVED
                    and recording.attempt_no < settings.recording_max_start_attempts)
    if retry_wanted:
        # The trainer's approval stands and the class is still running, so try
        # once more. The failed attempt is kept (never overwritten) for admins.
        recording.retry_count += 1
        run.recording_state = REC_APPROVED
        job_queue.enqueue(db, "recording.start", f"recording.start:{run.id}:attempt{recording.attempt_no + 1}",
                          {"run_id": run.id}, run_at=now)
        return "recording_failed_retrying"
    run.recording_state = REC_FAILED
    return "recording_failed"
