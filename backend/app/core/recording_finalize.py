"""Turns a finished capture into a VERIFIED stored recording (or an honest failure).

`FINALIZING` (the media server says it stopped writing) becomes `STORED` only
after the file on persistent staging storage passes real media verification.
Nothing is published from here: publication is a separate step that happens
only after the verified file is safely in its final home.
"""
import json
import logging
from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.core import job_queue
from app.core.class_runs import as_utc, utcnow
from app.core.class_lifecycle import lock_run
from app.core.job_queue import PermanentFailure, RetryLater
from app.core.recording_storage import StagingStore, StorageError
from app.core.recording_verify import verify_media_file
from app.crud.audit import write_audit_event
from app.models.audit_log import AuthEventType
from app.models.class_run import (
    CAPTURE_FAILED,
    CAPTURE_FINALIZING,
    CAPTURE_STORED,
    CAPTURE_VERIFYING,
    REC_FAILED,
    ClassRecording,
    RecordingEvent,
)

logger = logging.getLogger("recording_finalize")
FILE_WAIT_SECONDS = 15


def _event(db, recording, event, detail=None):
    db.add(RecordingEvent(run_id=recording.run_id, recording_id=recording.id, event=event,
                          detail=detail[:500] if detail else None))


def _fail(db: Session, recording: ClassRecording, code: str, detail: str) -> None:
    run = lock_run(db, recording.run_id)
    recording.capture_state = CAPTURE_FAILED
    recording.error_code, recording.error_detail = code[:64], detail[:500]
    if run is not None:
        run.recording_state = REC_FAILED
    _event(db, recording, "verification_failed", f"{code}: {detail}")
    write_audit_event(db, AuthEventType.recording_failed, None, None, detail=f"Recording failed ({code})",
                      module="recordings", target=f"run:{recording.run_id}", status="failure", commit=False)
    logger.error("recording failed run_id=%s code=%s", recording.run_id, code)


def finalize_recording(db: Session, recording_id: str | UUID) -> str:
    """Job body for `recording.finalize`. Commits its own phases so a long
    verification never sits inside a held row lock."""
    rid = UUID(str(recording_id))
    recording = db.get(ClassRecording, rid, with_for_update=True)
    if recording is None:
        return "no_such_recording"
    if recording.capture_state == CAPTURE_STORED:
        db.rollback()
        return "already_stored"
    if recording.capture_state not in (CAPTURE_FINALIZING, CAPTURE_VERIFYING):
        db.rollback()
        return "not_ready"

    store = StagingStore()
    try:
        path = store.path_for(recording.staging_key or "")
    except StorageError:
        _fail(db, recording, "bad_storage_key", "The recording has no valid storage location.")
        db.commit()
        return "failed"
    if not store.exists_nonempty(recording.staging_key):
        db.rollback()
        raise RetryLater("recording file not visible yet", delay_seconds=FILE_WAIT_SECONDS)

    recording.capture_state = CAPTURE_VERIFYING
    _event(db, recording, "verification_started")
    window = None
    if recording.started_at and recording.ended_at:
        window = (as_utc(recording.ended_at) - as_utc(recording.started_at)).total_seconds()
    db.commit()

    result = verify_media_file(path, expected_window_seconds=window)

    recording = db.get(ClassRecording, rid, with_for_update=True)
    if not result.ok:
        _fail(db, recording, f"verification_{result.error_code}", result.detail or "verification failed")
        db.commit()
        return "failed"

    recording.sha256 = store.sha256_of(path)
    recording.size_bytes = result.size_bytes
    recording.duration_seconds = int(round(result.duration_seconds or 0))
    recording.verified_at = utcnow()
    recording.capture_state = CAPTURE_STORED
    recording.storage_provider = StagingStore.provider
    recording.error_code = recording.error_detail = None
    previous = json.loads(recording.media_info or "{}")
    previous["verified"] = {"codec": result.video_codec, "width": result.width, "height": result.height,
                            "has_audio": result.has_audio, "packets": result.packets}
    recording.media_info = json.dumps(previous)
    _event(db, recording, "verified_and_stored", f"{result.duration_seconds}s, {result.size_bytes} bytes")
    write_audit_event(db, AuthEventType.recording_processed, None, None, detail="Recording verified and stored",
                      module="recordings", target=f"run:{recording.run_id}", status="success", commit=False)
    # The run stays PROCESSING: nothing is published until the verified file has
    # reached its final storage (see recording_publish).
    from app.core import recording_publish
    recording_publish.schedule_after_stored(db, recording)
    db.commit()
    return "stored"


def on_finalize_dead(db: Session, job) -> None:
    rid = json.loads(job.payload)["recording_id"]
    recording = db.get(ClassRecording, UUID(rid), with_for_update=True)
    if recording is not None and recording.capture_state != CAPTURE_STORED:
        _fail(db, recording, "finalize_failed", job.last_error or "The recording could not be verified.")
