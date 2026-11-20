"""Retention of LOCAL recording copies. Disabled by default.

What it may do: remove the local staging file of a recording after its
OneDrive copy has been proven identical. Nothing else: it never deletes remote
files, database rows, Class Videos entries or directories, and it never touches
a recording that is not PUBLISHED from a verified OneDrive copy.

Two independent switches must both be flipped by an operator:
  RECORDING_RETENTION_ENABLED=true   (default false)
  RECORDING_RETENTION_DRY_RUN=false  (default true: only reports what it would do)

A local file is removed only if EVERY safeguard holds, re-checked at the
moment of removal rather than trusted from earlier state:
  * the recording is PUBLISHED, not deleted, and stored in ONEDRIVE with a
    remote item id and a recorded remote verification;
  * the remote copy was verified at least N days ago (floor RETENTION_MIN_DAYS);
  * the recorded size and sha256 are present and the local file still matches
    both (so exactly the verified file is the one removed);
  * OneDrive is readable right now, and the remote item's size AND
    QuickXorHash equal the local file's. Any error, mismatch or unreadable
    remote means "keep the local copy".
After removal playback continues from OneDrive (see routes_recordings).
"""
import logging
from datetime import timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.class_runs import as_utc, utcnow
from app.core.onedrive_store import OneDriveError, OneDriveStore
from app.core.recording_storage import StagingStore, StorageError
from app.models.class_run import (CAPTURE_STORED, PUBLICATION_PUBLISHED, ClassRecording, RecordingEvent)

logger = logging.getLogger("recording_retention")

RETENTION_MIN_DAYS = 7


def effective_days() -> int:
    return max(int(settings.recording_retention_local_days), RETENTION_MIN_DAYS)


def _event(db: Session, rec: ClassRecording, event: str, detail: str | None = None) -> None:
    db.add(RecordingEvent(run_id=rec.run_id, recording_id=rec.id, event=event, detail=detail[:500] if detail else None))


def _static_skip_reason(rec: ClassRecording, cutoff) -> str | None:
    """Cheap, database-only checks. Returns why NOT to touch the file, or None."""
    if rec.capture_state != CAPTURE_STORED or rec.publication_state != PUBLICATION_PUBLISHED:
        return "not_published"
    if rec.deleted_at is not None:
        return "deleted_record"
    if rec.storage_provider != OneDriveStore.provider or not rec.remote_item_id or rec.remote_verified_at is None:
        return "no_verified_remote_copy"
    if not rec.staging_key or not rec.size_bytes or not rec.sha256:
        return "missing_integrity_record"
    if as_utc(rec.remote_verified_at) > cutoff:
        return "too_recent"
    return None


def run_retention(db: Session, *, store: OneDriveStore | None = None, staging: StagingStore | None = None,
                  now=None, enabled: bool | None = None, dry_run: bool | None = None) -> dict:
    """One bounded pass. Returns counters; never raises for a single file's problem."""
    enabled = settings.recording_retention_enabled if enabled is None else enabled
    dry_run = settings.recording_retention_dry_run if dry_run is None else dry_run
    report: dict = {"enabled": bool(enabled), "dry_run": bool(dry_run), "examined": 0,
                    "would_remove": 0, "removed": 0, "kept": 0}
    reasons: dict[str, int] = {}
    if not enabled:
        return report
    store = store or OneDriveStore()
    staging = staging or StagingStore()
    cutoff = (now or utcnow()) - timedelta(days=effective_days())
    limit = max(int(settings.recording_retention_batch_limit), 1)

    candidates = db.scalars(select(ClassRecording.id).where(
        ClassRecording.capture_state == CAPTURE_STORED, ClassRecording.publication_state == PUBLICATION_PUBLISHED,
        ClassRecording.deleted_at.is_(None), ClassRecording.remote_verified_at.is_not(None),
        ClassRecording.remote_verified_at <= cutoff).order_by(ClassRecording.remote_verified_at).limit(limit)).all()
    for rid in candidates:
        report["examined"] += 1
        outcome = _consider(db, rid, store, staging, cutoff, dry_run)
        if outcome in ("would_remove", "removed"):
            report[outcome] += 1
        else:
            report["kept"] += 1
            reasons[outcome] = reasons.get(outcome, 0) + 1
    report["kept_reasons"] = reasons
    return report


def _consider(db: Session, rid: UUID, store: OneDriveStore, staging: StagingStore, cutoff, dry_run: bool) -> str:
    rec = db.get(ClassRecording, rid, with_for_update=True)
    try:
        if rec is None:
            return "gone"
        why = _static_skip_reason(rec, cutoff)
        if why:
            return why
        try:
            path = staging.path_for(rec.staging_key)
        except StorageError:
            return "unsafe_key"
        if not path.is_file():
            return "already_absent"  # nothing to remove; playback already uses the remote copy
        if path.stat().st_size != rec.size_bytes:
            return "local_size_mismatch"
        if staging.sha256_of(path) != rec.sha256:
            return "local_hash_mismatch"
        try:
            store.verify_remote(rec.remote_item_id, path)  # size + QuickXorHash, read-only, right now
        except OneDriveError as exc:
            logger.warning("retention kept local copy: remote not verified recording=%s error=%s", rec.id, type(exc).__name__)
            _event(db, rec, "retention_kept_remote_unverified", str(exc))
            db.commit()
            return "remote_unverified"
        if dry_run:
            return "would_remove"
        path.unlink()  # exactly one verified file; no globbing, no directories
        _event(db, rec, "local_copy_removed", f"{rec.size_bytes} bytes; remote copy re-verified")
        db.commit()
        logger.info("retention removed local copy recording=%s", rec.id)
        return "removed"
    except Exception:  # noqa: BLE001 - one bad file must never stop the pass or cause a deletion
        db.rollback()
        logger.exception("retention failed safely for recording=%s; local copy kept", rid)
        return "error"
    finally:
        if db.in_transaction():
            db.rollback()
