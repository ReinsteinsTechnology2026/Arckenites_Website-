"""Lifecycle of a class occurrence: activation, presence, grace period, end.

Rules this module enforces (all server-side, none trusting the browser):

* Opening a room records nothing and creates no attendance. A class only
  becomes LIVE when the trainer is actually connected.
* A trainer who drops is given `trainer_reconnect_grace_seconds` to return.
  Rejoining cancels the pending termination; otherwise the class ends for
  everyone and any active recording is finalized.
* The trainer ending the class, or leaving it for good, ends it immediately.
* A class nobody led by its scheduled end is MISSED and never recorded.
* Nothing here starts a recording. Ending a class only ever STOPS one.

Functions take the occurrence's row lock and do NOT commit; the caller (an API
route or a job) commits once, so a state change and the jobs it enqueues land
atomically. Every function is idempotent because job delivery is at-least-once
and presence webhooks can repeat or arrive out of order.
"""
import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.core import class_runs, job_queue
from app.core.class_runs import as_utc, occurrence_key_for_session, session_window, utcnow
from app.core.job_queue import RetryLater
from app.core.scheduling import business_tz
from app.crud.audit import write_audit_event
from app.models.audit_log import AuthEventType
from app.models.batch import Batch, BatchStatusEnum
from app.models.class_run import (
    RUN_CANCELLED,
    RUN_ENDED,
    RUN_KIND_AD_HOC,
    RUN_KIND_SCHEDULED,
    RUN_LIVE,
    RUN_MISSED,
    RUN_OPEN_STATES,
    RUN_ROOM_OPEN,
    RUN_SCHEDULED,
    RUN_TERMINAL_STATES,
    REC_APPROVED,
    REC_AWAITING_APPROVAL,
    REC_NOT_RECORDED,
    REC_RECORDING,
    REC_STARTING,
    REC_STOPPING,
    ClassSessionRun,
)
from app.models.class_session import ClassSession
from app.models.meeting import Meeting, MeetingMessage, MeetingStatus, MeetingParticipant, ParticipantStatus
from app.models.user import User

logger = logging.getLogger("class_lifecycle")

END_TRAINER_ENDED = "TRAINER_ENDED"
END_TRAINER_LEFT = "TRAINER_LEFT"
END_TRAINER_TIMEOUT = "TRAINER_ABSENT_TIMEOUT"
END_SCHEDULED = "SCHEDULED_END"
END_NO_SHOW = "NO_SHOW"
ELIGIBLE_BATCH_STATUSES = (BatchStatusEnum.upcoming, BatchStatusEnum.active)
RECONCILE_DAYS_AHEAD = 2


def grace_seconds() -> int:
    return settings.trainer_reconnect_grace_seconds


def lock_run(db: Session, run_id: int) -> ClassSessionRun | None:
    return db.scalar(select(ClassSessionRun).where(ClassSessionRun.id == run_id).with_for_update())


def _log(db: Session, run: ClassSessionRun, event: str, **kwargs) -> None:
    class_runs._log_event(db, run, event, **kwargs)


def _epoch(value: datetime) -> int:
    return int(as_utc(value).timestamp())


# --- job scheduling ---------------------------------------------------------

def scheduled_end_key(run_id: int, end_at: datetime) -> str:
    return f"run.scheduled_end:{run_id}:{_epoch(end_at)}"


def schedule_run_jobs(db: Session, run: ClassSessionRun, now: datetime | None = None) -> None:
    """Idempotently ensures the time-driven jobs for an occurrence exist. Safe
    to call repeatedly (every job is deduplicated by key)."""
    now = now or utcnow()
    if run.state == RUN_SCHEDULED:
        opens = max(as_utc(run.scheduled_start_at) - timedelta(minutes=settings.class_room_open_lead_minutes), now)
        job_queue.enqueue(db, "session.activate", f"session.activate:{run.id}", {"run_id": run.id}, run_at=opens)
    if run.state in (RUN_SCHEDULED, *RUN_OPEN_STATES):
        end_at = as_utc(run.effective_end_at)
        job_queue.enqueue(db, "run.scheduled_end", scheduled_end_key(run.id, end_at),
                          {"run_id": run.id, "end_at": end_at.isoformat()}, run_at=end_at)
    if run.state == RUN_LIVE and run.trainer_left_at is not None:
        _enqueue_grace_job(db, run, as_utc(run.trainer_left_at))


def _enqueue_grace_job(db: Session, run: ClassSessionRun, left_at: datetime) -> None:
    job_queue.enqueue(
        db, "run.grace_expiry", f"run.grace:{run.id}:{int(left_at.timestamp() * 1000)}",
        {"run_id": run.id, "left_at": left_at.isoformat()}, run_at=left_at + timedelta(seconds=grace_seconds()),
    )


def open_adhoc_run(db: Session, batch: Batch, now: datetime | None = None) -> ClassSessionRun:
    """The ad-hoc entry point: same occurrence, approval and lifecycle as a
    scheduled class. Does not commit."""
    now = now or utcnow()
    run = class_runs.get_or_create_adhoc_run(db, batch, now, commit=False)
    schedule_run_jobs(db, run, now)
    return run


# --- transitions ------------------------------------------------------------

def _session_still_matches(db: Session, run: ClassSessionRun) -> str | None:
    """None if a scheduled occurrence still matches its session; otherwise the
    reason it is stale (deleted or rescheduled)."""
    if run.kind != RUN_KIND_SCHEDULED:
        return None
    session = db.get(ClassSession, run.class_session_id) if run.class_session_id else None
    if session is None:
        return "SESSION_DELETED"
    window = session_window(session)
    if window is None or occurrence_key_for_session(session.id, window[0]) != run.occurrence_key:
        return "RESCHEDULED"
    return None


def activate_run(db: Session, run_id: int, now: datetime | None = None) -> bool:
    """SCHEDULED -> ROOM_OPEN. Creates no attendance and starts no recording."""
    now = now or utcnow()
    run = lock_run(db, run_id)
    if run is None or run.state != RUN_SCHEDULED:
        return False
    stale = _session_still_matches(db, run)
    if stale is not None:
        cancel_run(db, run, stale, now=now)
        return False
    run.state = RUN_ROOM_OPEN
    _log(db, run, "room_opened", from_state=RUN_SCHEDULED, to_state=RUN_ROOM_OPEN)
    job_queue.enqueue(db, "run.notify_open", f"run.notify_open:{run.id}", {"run_id": run.id}, run_at=now)
    return True


def end_run(db: Session, run: ClassSessionRun, reason: str, *, actor: User | None = None,
            now: datetime | None = None, final_state: str = RUN_ENDED) -> bool:
    """Ends the class for everyone and starts finalizing any active recording.
    Caller holds the row lock. False if it had already ended (idempotent)."""
    now = now or utcnow()
    if run.state in RUN_TERMINAL_STATES:
        return False
    previous = run.state
    run.state = final_state
    run.actual_end_at = now
    run.end_reason = reason
    run.trainer_left_at = None

    recording_before = run.recording_state
    if recording_before in (REC_AWAITING_APPROVAL, REC_APPROVED):
        # Never approved, or approved but the recording had not started: it
        # will not start now (the start job re-checks that the class is open).
        run.recording_state = REC_NOT_RECORDED
    elif recording_before in (REC_STARTING, REC_RECORDING):
        run.recording_state = REC_STOPPING
        job_queue.enqueue(db, "recording.stop", f"recording.stop:{run.id}", {"run_id": run.id}, run_at=now)

    if run.meeting_id:
        meeting = db.get(Meeting, run.meeting_id)
        if meeting is not None and meeting.status in (MeetingStatus.SCHEDULED, MeetingStatus.LIVE):
            meeting.status = MeetingStatus.COMPLETED
            meeting.ended_at = now
            for participant in db.scalars(select(MeetingParticipant).where(
                MeetingParticipant.meeting_id == meeting.id,
                MeetingParticipant.status.in_((ParticipantStatus.JOINED, ParticipantStatus.WAITING, ParticipantStatus.ADMITTED)),
            )).all():
                participant.status, participant.left_at = ParticipantStatus.LEFT, now
            db.add(MeetingMessage(meeting_id=meeting.id, user_id=None, message="The class has ended.", is_system=True))

    if previous != RUN_SCHEDULED:
        job_queue.enqueue(db, "room.close", f"room.close:{run.id}", {"run_id": run.id}, run_at=now)

    _log(db, run, "run_ended", from_state=previous, to_state=final_state, actor=actor, detail=reason)
    write_audit_event(db, AuthEventType.meeting_ended, None, None, user=actor,
                      detail=f"Class ended ({reason})", module="class_runs", target=f"run:{run.id}",
                      status="success", commit=False)
    return True


def cancel_run(db: Session, run: ClassSessionRun, reason: str, *, now: datetime | None = None) -> bool:
    """Cancels an occurrence (rescheduled, session deleted, batch paused...).
    A class that is already running is ended instead. Caller holds the lock."""
    now = now or utcnow()
    if run.state in RUN_TERMINAL_STATES:
        return False
    if run.state == RUN_LIVE or run.actual_start_at is not None:
        return end_run(db, run, reason, now=now)
    previous = run.state
    run.state = RUN_CANCELLED
    run.end_reason = reason
    run.actual_end_at = None
    if run.recording_state in (REC_AWAITING_APPROVAL, REC_APPROVED):
        run.recording_state = REC_NOT_RECORDED
    _log(db, run, "run_cancelled", from_state=previous, to_state=RUN_CANCELLED, detail=reason)
    write_audit_event(db, AuthEventType.class_run_cancelled, None, None, detail=f"Class cancelled ({reason})",
                      module="class_runs", target=f"run:{run.id}", status="success", commit=False)
    return True


def trainer_end(db: Session, run_id: int, actor: User, reason: str = END_TRAINER_ENDED,
                now: datetime | None = None) -> ClassSessionRun | None:
    """The trainer ends the class, or leaves it permanently: ends for everyone
    right now and initiates recording finalization immediately."""
    run = lock_run(db, run_id)
    if run is not None:
        end_run(db, run, reason, actor=actor, now=now)
    return run


def mark_trainer_connected(db: Session, run_id: int, *, event_at: datetime | None = None,
                           now: datetime | None = None) -> bool:
    """Authenticated media-server event: the trainer is in the room. Cancels
    any pending grace-period termination and makes the class LIVE."""
    now = now or utcnow()
    run = lock_run(db, run_id)
    if run is None or run.state in RUN_TERMINAL_STATES:
        return False
    if _is_stale_presence(run, event_at, connecting=True):
        return False
    if (event_at is not None and run.trainer_presence_event_at is not None
            and as_utc(event_at) == as_utc(run.trainer_presence_event_at) and run.trainer_left_at is None
            and run.state == RUN_LIVE):
        return False  # an exact duplicate of an event already applied
    run.trainer_presence_event_at = event_at or now
    run.trainer_left_at = None  # any queued grace job now finds nothing to expire
    if run.state != RUN_LIVE:
        previous = run.state
        run.state = RUN_LIVE
        run.actual_start_at = run.actual_start_at or now
        _log(db, run, "trainer_joined", from_state=previous, to_state=RUN_LIVE)
    else:
        _log(db, run, "trainer_reconnected")
    return True


def mark_trainer_disconnected(db: Session, run_id: int, *, event_at: datetime | None = None,
                              now: datetime | None = None) -> bool:
    """Authenticated media-server event: the trainer's connection is gone. This
    starts the grace period; it does NOT end the class."""
    now = now or utcnow()
    run = lock_run(db, run_id)
    if run is None or run.state != RUN_LIVE:
        return False
    if _is_stale_presence(run, event_at, connecting=False):
        return False
    run.trainer_presence_event_at = event_at or now
    # The grace period runs from when we learned of the drop, so a delayed
    # webhook cannot end a class before the trainer had a fair window.
    run.trainer_left_at = now
    _enqueue_grace_job(db, run, now)
    _log(db, run, "trainer_disconnected", detail=f"grace {grace_seconds()}s")
    return True


def _is_stale_presence(run: ClassSessionRun, event_at: datetime | None, *, connecting: bool) -> bool:
    """Media-server timestamps have one-second resolution, so a drop and a
    reconnect in the same second tie. A tie must never leave the trainer marked
    absent: a 'connected' event is stale only if strictly older than the last
    applied event, a 'disconnected' one if older OR equal."""
    if event_at is None or run.trainer_presence_event_at is None:
        return False
    newest, incoming = as_utc(run.trainer_presence_event_at), as_utc(event_at)
    return incoming < newest if connecting else incoming <= newest


def grace_expired(db: Session, run_id: int, left_at: datetime, now: datetime | None = None) -> bool:
    now = now or utcnow()
    run = lock_run(db, run_id)
    if run is None or run.state != RUN_LIVE or run.trainer_left_at is None:
        return False  # ended already, or the trainer came back
    if as_utc(run.trainer_left_at) != as_utc(left_at):
        return False  # a different (newer) departure owns the termination
    deadline = as_utc(left_at) + timedelta(seconds=grace_seconds())
    if now < deadline:
        raise RetryLater("grace period still running", delay_seconds=max((deadline - now).total_seconds(), 1.0))
    return end_run(db, run, END_TRAINER_TIMEOUT, now=now)


def scheduled_end(db: Session, run_id: int, end_at: datetime, now: datetime | None = None) -> bool:
    now = now or utcnow()
    run = lock_run(db, run_id)
    if run is None or run.state in RUN_TERMINAL_STATES:
        return False
    if as_utc(run.effective_end_at) != as_utc(end_at):
        return False  # the class was extended; a job for the new end exists
    if now < as_utc(end_at):
        raise RetryLater("not yet the scheduled end", delay_seconds=max((as_utc(end_at) - now).total_seconds(), 1.0))
    if run.state == RUN_LIVE:
        return end_run(db, run, END_SCHEDULED, now=now)
    # Nobody led it. It is recorded as missed, never as a class that happened.
    return end_run(db, run, END_NO_SHOW, now=now, final_state=RUN_MISSED)


def extend_run(db: Session, run_id: int, actor: User, minutes: int, now: datetime | None = None) -> ClassSessionRun:
    from fastapi import HTTPException, status
    now = now or utcnow()
    run = lock_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Class not found")
    if run.state not in RUN_OPEN_STATES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only a class that is open can be extended.")
    already = (as_utc(run.effective_end_at) - as_utc(run.scheduled_end_at)).total_seconds() / 60
    if minutes < 1 or already + minutes > settings.class_extension_max_minutes:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=f"A class can be extended by at most {settings.class_extension_max_minutes} minutes in total.")
    run.effective_end_at = as_utc(run.effective_end_at) + timedelta(minutes=minutes)
    schedule_run_jobs(db, run, now)
    _log(db, run, "run_extended", actor=actor, detail=f"+{minutes} min")
    write_audit_event(db, AuthEventType.meeting_updated, None, None, user=actor,
                      detail=f"Class extended by {minutes} minutes", module="class_runs",
                      target=f"run:{run.id}", status="success", commit=False)
    return run


# --- reconciliation (the safety net) ---------------------------------------

def reconcile_sessions(db: Session, now: datetime | None = None) -> dict[str, int]:
    """Brings occurrences in line with the authoritative class schedule, and
    re-creates any time-driven job that was lost. Runs every few seconds from
    the worker and is safe to run concurrently or repeatedly. This is what
    makes new, rescheduled, cancelled, deleted and paused sessions, missed
    jobs after an outage, and worker restarts all converge without a browser."""
    now = now or utcnow()
    counts = {"created": 0, "cancelled": 0, "jobs_checked": 0}
    zone = business_tz()
    today = now.astimezone(zone).date()
    dates = [today + timedelta(days=offset) for offset in range(-1, RECONCILE_DAYS_AHEAD + 1)]

    sessions = db.scalars(select(ClassSession).where(
        ClassSession.session_date.in_(dates), ClassSession.start_time.is_not(None), ClassSession.end_time.is_not(None),
    )).all()
    for session in sessions:
        window = session_window(session)
        if window is None or window[1] <= now:
            continue
        batch = db.get(Batch, session.batch_id)
        if batch is None or batch.status not in ELIGIBLE_BATCH_STATUSES or batch.trainer_id is None:
            continue  # paused/cancelled/completed batch or no trainer: not eligible
        key = occurrence_key_for_session(session.id, window[0])
        known = db.scalar(select(ClassSessionRun.id).where(ClassSessionRun.occurrence_key == key)) is not None
        run = class_runs.get_or_create_session_run(db, session, batch, commit=False)
        counts["created"] += 0 if known else 1
        if run.state == RUN_CANCELLED and run.end_reason == "BATCH_INELIGIBLE":
            # The batch was paused and has since resumed before the class began.
            # The interruption voids any earlier decision: approval is asked again.
            run = lock_run(db, run.id)
            run.state, run.end_reason = RUN_SCHEDULED, None
            run.recording_state, run.approval_status = REC_AWAITING_APPROVAL, "PENDING"
            run.approval_source = run.approval_decided_by = run.approval_decided_at = None
            run.approval_prompt_shown_at = run.consent_text_version = None
            _log(db, run, "run_revived", from_state=RUN_CANCELLED, to_state=RUN_SCHEDULED)
        if run.state == RUN_SCHEDULED:
            schedule_run_jobs(db, run, now)

    open_runs = db.scalars(select(ClassSessionRun).where(
        ClassSessionRun.state.in_((RUN_SCHEDULED, *RUN_OPEN_STATES)))).all()
    for run in open_runs:
        locked = lock_run(db, run.id)
        if locked is None or locked.state in RUN_TERMINAL_STATES:
            continue
        run = locked
        if run.kind == RUN_KIND_SCHEDULED and run.state in (RUN_SCHEDULED, RUN_ROOM_OPEN) and run.actual_start_at is None:
            stale = _session_still_matches(db, run)
            if stale is None:
                batch = db.get(Batch, run.batch_id)
                if batch is None or batch.status not in ELIGIBLE_BATCH_STATUSES or batch.trainer_id is None:
                    stale = "BATCH_INELIGIBLE"
            if stale is not None:
                counts["cancelled"] += int(cancel_run(db, run, stale, now=now))
                continue
        schedule_run_jobs(db, run, now)
        counts["jobs_checked"] += 1
    db.flush()
    return counts
