"""Class occurrences and the trainer's recording approval.

Everything that decides whether a class may be recorded lives here, on the
server. The browser only ever shows the dialog and sends the decision back;
it cannot grant consent, skip the dialog, or approve on a trainer's behalf.

Concurrency model: every state change takes a row lock on the occurrence
(`SELECT ... FOR UPDATE`, a no-op on SQLite) and the background job is keyed
by a unique `dedupe_key`, so duplicate requests, retries and two tabs can
never produce two recordings.
"""
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.core import job_queue
from app.core.recording_storage import StagingStore
from app.core.scheduling import local_to_utc
from app.crud.audit import write_audit_event
from app.crud.permissions import user_has_permission
from app.models.audit_log import AuthEventType
from app.models.batch import Batch, BatchEnrollment
from app.models.class_run import (
    APPROVAL_APPROVED,
    APPROVAL_DECLINED,
    APPROVAL_PENDING,
    APPROVER_TRAINER,
    REC_APPROVED,
    REC_AWAITING_APPROVAL,
    REC_DECLINED,
    REC_FAILED,
    REC_NOT_RECORDED,
    REC_STARTING,
    RUN_CANCELLED,
    RUN_KIND_AD_HOC,
    RUN_KIND_SCHEDULED,
    RUN_OPEN_STATES,
    RUN_ROOM_OPEN,
    RUN_SCHEDULED,
    RUN_TERMINAL_STATES,
    ClassRecording,
    ClassSessionRun,
    RecordingEvent,
)
from app.models.class_session import ClassSession
from app.models.user import RoleEnum, User

UTC = timezone.utc
AD_HOC_DEFAULT_MINUTES = 120

# Shown verbatim in the trainer's dialog. The version string in settings is
# what the server compares, so changing this wording means bumping that too.
CONSENT_TEXT = (
    "This class will be recorded. The recording will be securely stored in Arckenites and "
    "made available to authorized students in this batch's Class Videos section. "
    "All participants are notified that recording is active."
)

CONTROLLER_TRAINER = "TRAINER"


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes for timezone-aware columns; PostgreSQL
    does not. Normalise so comparisons behave identically on both."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


# ---------------------------------------------------------------------------
# Occurrences
# ---------------------------------------------------------------------------

def session_window(class_session: ClassSession) -> tuple[datetime, datetime] | None:
    """Authoritative UTC start/end of a class session, or None when the
    session has no usable times (it is then ineligible, never guessed at)."""
    if class_session.start_time is None or class_session.end_time is None:
        return None
    start = local_to_utc(class_session.session_date, class_session.start_time)
    end = local_to_utc(class_session.session_date, class_session.end_time)
    if end <= start:  # a class that runs past local midnight
        end = local_to_utc(class_session.session_date + timedelta(days=1), class_session.end_time)
    return start, end


def occurrence_key_for_session(class_session_id: int, start_utc: datetime) -> str:
    return f"session:{class_session_id}:{start_utc.astimezone(UTC):%Y%m%dT%H%M%SZ}"


def _log_event(db: Session, run: ClassSessionRun, event: str, *, from_state: str | None = None,
               to_state: str | None = None, actor: User | None = None, detail: str | None = None) -> None:
    db.add(RecordingEvent(
        run_id=run.id, event=event, from_state=from_state, to_state=to_state,
        actor_user_id=actor.id if actor else None, detail=detail[:500] if detail else None,
    ))


def _select_run(db: Session, run_id: int, *, lock: bool = False) -> ClassSessionRun | None:
    query = select(ClassSessionRun).where(ClassSessionRun.id == run_id)
    if lock:
        query = query.with_for_update()
    return db.scalar(query)


def supersede_stale_runs(db: Session, class_session_id: int, keep_key: str) -> int:
    """A rescheduled session gets a new occurrence key. Any older occurrence of
    the same session that has not started is cancelled, so its approval can
    never carry over to the new time (rescheduling requires a new approval)."""
    stale = db.scalars(
        select(ClassSessionRun).where(
            ClassSessionRun.class_session_id == class_session_id,
            ClassSessionRun.occurrence_key != keep_key,
            ClassSessionRun.state.in_((RUN_SCHEDULED, RUN_ROOM_OPEN)),
            ClassSessionRun.actual_start_at.is_(None),
        ).with_for_update()
    ).all()
    for run in stale:
        previous = run.state
        run.state = RUN_CANCELLED
        run.end_reason = "RESCHEDULED"
        run.recording_state = REC_NOT_RECORDED
        _log_event(db, run, "run_superseded", from_state=previous, to_state=RUN_CANCELLED,
                   detail="Class session was rescheduled; a new occurrence requires its own approval.")
    return len(stale)


def get_or_create_session_run(
    db: Session, class_session: ClassSession, batch: Batch, *, commit: bool = True
) -> ClassSessionRun:
    window = session_window(class_session)
    if window is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="This class session has no start and end time, so it can't be started.")
    start, end = window
    key = occurrence_key_for_session(class_session.id, start)

    existing = db.scalar(select(ClassSessionRun).where(ClassSessionRun.occurrence_key == key))
    if existing is None:
        try:
            with db.begin_nested():
                existing = ClassSessionRun(
                    occurrence_key=key, kind=RUN_KIND_SCHEDULED, class_session_id=class_session.id,
                    batch_id=batch.id, trainer_id=batch.trainer_id,
                    scheduled_start_at=start, scheduled_end_at=end, effective_end_at=end,
                    state=RUN_SCHEDULED, approval_status=APPROVAL_PENDING, recording_state=REC_AWAITING_APPROVAL,
                )
                db.add(existing)
        except IntegrityError:  # another request created it first: use theirs
            existing = db.scalar(select(ClassSessionRun).where(ClassSessionRun.occurrence_key == key))
    supersede_stale_runs(db, class_session.id, key)
    if commit:
        db.commit()
    else:
        db.flush()
    return existing


def get_or_create_adhoc_run(
    db: Session, batch: Batch, now: datetime | None = None, *, commit: bool = True
) -> ClassSessionRun:
    """An ad-hoc batch class (started with no ClassSession row) is still an
    occurrence, so it goes through exactly the same approval workflow. A batch
    has at most one open ad-hoc occurrence at a time (enforced by a partial
    unique index, not only by this query)."""
    now = now or utcnow()
    open_run = db.scalar(select(ClassSessionRun).where(
        ClassSessionRun.batch_id == batch.id, ClassSessionRun.kind == RUN_KIND_AD_HOC,
        ClassSessionRun.state.in_(RUN_OPEN_STATES),
    ))
    if open_run is not None:
        return open_run
    try:
        with db.begin_nested():
            run = ClassSessionRun(
                occurrence_key=f"adhoc:{batch.id}:{uuid.uuid4().hex}", kind=RUN_KIND_AD_HOC,
                batch_id=batch.id, trainer_id=batch.trainer_id,
                scheduled_start_at=now, scheduled_end_at=now + timedelta(minutes=AD_HOC_DEFAULT_MINUTES),
                effective_end_at=now + timedelta(minutes=AD_HOC_DEFAULT_MINUTES),
                state=RUN_ROOM_OPEN, approval_status=APPROVAL_PENDING,
                recording_state=REC_AWAITING_APPROVAL,
            )
            db.add(run)
    except IntegrityError:
        run = db.scalar(select(ClassSessionRun).where(
            ClassSessionRun.batch_id == batch.id, ClassSessionRun.kind == RUN_KIND_AD_HOC,
            ClassSessionRun.state.in_(RUN_OPEN_STATES),
        ))
    if commit:
        db.commit()
    else:
        db.flush()
    return run


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------

def recording_controller(db: Session, user: User, batch: Batch) -> str | None:
    """Who may approve, decline, start, end or extend a class: ONLY the
    batch's current trainer. There is deliberately no admin override and
    students can never control a class or its recording."""
    if user.role == RoleEnum.staff and batch.trainer_id == user.id:
        return CONTROLLER_TRAINER
    return None


def is_enrolled(db: Session, user: User, batch: Batch) -> bool:
    return db.scalar(select(BatchEnrollment.id).where(
        BatchEnrollment.batch_id == batch.id, BatchEnrollment.student_id == user.id
    )) is not None


def can_view_status(db: Session, user: User, batch: Batch) -> bool:
    if recording_controller(db, user, batch):
        return True
    if user.role == RoleEnum.admin and user_has_permission(db, user, "recordings.view"):
        return True
    return user.role == RoleEnum.student and is_enrolled(db, user, batch)


# ---------------------------------------------------------------------------
# Infrastructure gate
# ---------------------------------------------------------------------------

def recording_unavailable_reason() -> str | None:
    """None when recording can genuinely be started right now; otherwise an
    accurate, user-safe reason. Checked BEFORE approval is persisted so a
    trainer is never told a class is recording when it cannot be."""
    if not settings.recording_enabled:
        return "Class recording isn't enabled on this server."
    if not settings.livekit_configured:
        return "The meeting media server isn't configured."
    ready, reason = StagingStore().readiness()
    return None if ready else reason


# ---------------------------------------------------------------------------
# What the trainer is told when recording is not going to plan
# ---------------------------------------------------------------------------

EVENT_WAITING_FOR_MEDIA = "recording_waiting_for_media"
NOTICE_WAITING = "waiting_for_media"
FAILURE_NOTICES = {
    "no_media_published": (
        "Recording could not start because your camera, microphone and screen share were all off. "
        "Turn one on, then choose Retry recording."),
    "start_timeout": (
        "The recorder joined the class but never began recording. The class continues. "
        "Check that your camera or microphone is on, then choose Retry recording."),
    "egress_lost": (
        "The recording stopped unexpectedly. The class continues. Choose Retry recording to start a new recording."),
}
GENERIC_FAILURE_NOTICE = "Recording could not be started. The class continues without a recording."
WAITING_NOTICE = ("Waiting for your camera, microphone or screen share. "
                  "Recording starts as soon as one is on.")


def recording_notice(db: Session, run: ClassSessionRun) -> tuple[str | None, str | None]:
    """(code, text) explaining why recording has not started or has stopped, or
    (None, None). Only ever shown to the trainer who controls recording."""
    latest = db.scalar(select(ClassRecording).where(ClassRecording.run_id == run.id)
                       .order_by(ClassRecording.attempt_no.desc()).limit(1))
    if latest is None:
        return None, None
    if run.recording_state in (REC_APPROVED, REC_STARTING) and latest.egress_id is None:
        waiting = db.scalar(select(RecordingEvent.id).where(
            RecordingEvent.recording_id == latest.id, RecordingEvent.event == EVENT_WAITING_FOR_MEDIA))
        if waiting is not None:
            return NOTICE_WAITING, WAITING_NOTICE
    if run.recording_state == REC_FAILED:
        code = latest.error_code or "failed"
        return code, FAILURE_NOTICES.get(code, GENERIC_FAILURE_NOTICE)
    return None, None


def can_retry_recording(run: ClassSessionRun) -> bool:
    return (run.approval_status == APPROVAL_APPROVED and run.recording_state == REC_FAILED
            and run.state in RUN_OPEN_STATES)


def _retry_failed_recording(db: Session, user: User, run: ClassSessionRun, consent_version: str | None,
                            ip: str | None, ua: str | None) -> tuple[ClassSessionRun, bool]:
    """The trainer asks again after a failed recording while the class is still
    open. This is a fresh, explicit trainer action: it needs the current
    consent wording like the first approval, and it starts one new attempt
    (the failed attempts stay on record)."""
    if consent_version != settings.recording_consent_version:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="The recording notice has changed. Reload the page and review it again.")
    unavailable = recording_unavailable_reason()
    if unavailable is not None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=unavailable)
    attempts = len(db.scalars(select(ClassRecording.id).where(ClassRecording.run_id == run.id)).all())
    run.recording_state = REC_APPROVED
    run.approval_decided_by, run.approval_decided_at = user.id, utcnow()
    run.consent_text_version = consent_version
    _log_event(db, run, "recording_retry_requested", from_state=REC_FAILED, to_state=REC_APPROVED, actor=user)
    job_queue.enqueue(db, "recording.start", f"recording.start:{run.id}:retry{attempts}", {"run_id": run.id})
    write_audit_event(db, AuthEventType.recording_approval_granted, ip, ua, user=user,
                      detail="Recording retried by the class trainer", module="recordings",
                      target=f"run:{run.id}", status="success", commit=False)
    db.commit()
    return run, True


# ---------------------------------------------------------------------------
# Prompt + approval
# ---------------------------------------------------------------------------

def claim_prompt(db: Session, user: User, run: ClassSessionRun, ip: str | None, ua: str | None) -> bool:
    """Atomically claims the one-time right to show the approval dialog for
    this occurrence. True exactly once; every refresh, reconnect and second
    tab gets False afterwards. A dismissed dialog therefore never re-opens,
    and the persistent Start Recording control takes over."""
    if run.state not in RUN_OPEN_STATES or run.approval_status != APPROVAL_PENDING:
        return False
    claimed = db.execute(
        update(ClassSessionRun)
        .where(ClassSessionRun.id == run.id, ClassSessionRun.approval_prompt_shown_at.is_(None),
               ClassSessionRun.approval_status == APPROVAL_PENDING)
        .values(approval_prompt_shown_at=utcnow())
    ).rowcount == 1
    if claimed:
        _log_event(db, run, "approval_requested", actor=user)
        write_audit_event(db, AuthEventType.recording_approval_requested, ip, ua, user=user,
                          detail="Recording approval requested", module="recordings",
                          target=f"run:{run.id}", status="success", commit=False)
    db.commit()
    db.refresh(run)
    return claimed


def decide_approval(
    db: Session, user: User, run_id: int, *, decision: str,
    consent_version: str | None, ip: str | None, ua: str | None,
) -> tuple[ClassSessionRun, bool]:
    """Records the class trainer's explicit decision.

    Returns (run, changed). `changed` is False for an idempotent repeat of a
    decision that is already stored, in which case nothing is written and no
    second job is created.
    """
    run = _select_run(db, run_id, lock=True)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Class not found")
    if run.state in RUN_TERMINAL_STATES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This class has already ended or was cancelled.")

    if decision == "decline":
        if run.approval_status == APPROVAL_DECLINED:
            return run, False
        if run.approval_status == APPROVAL_APPROVED:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail="Recording was already approved. Stop the recording instead.")
        run.approval_status = APPROVAL_DECLINED
        run.recording_state = REC_DECLINED
        run.approval_source, run.approval_decided_by = APPROVER_TRAINER, user.id
        run.approval_decided_at = utcnow()
        _log_event(db, run, "approval_declined", from_state=REC_AWAITING_APPROVAL, to_state=REC_DECLINED, actor=user)
        write_audit_event(db, AuthEventType.recording_approval_declined, ip, ua, user=user,
                          detail="Recording declined", module="recordings", target=f"run:{run.id}",
                          status="success", commit=False)
        db.commit()
        return run, True

    # decision == "approve"
    if run.approval_status == APPROVAL_APPROVED:
        if run.recording_state == REC_FAILED and run.state in RUN_OPEN_STATES:
            return _retry_failed_recording(db, user, run, consent_version, ip, ua)
        return run, False
    if run.state not in RUN_OPEN_STATES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This class hasn't opened yet.")
    if consent_version != settings.recording_consent_version:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="The recording notice has changed. Reload the page and review it again.")
    unavailable = recording_unavailable_reason()
    if unavailable is not None:
        # Nothing is persisted: the trainer is told the truth and can retry.
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=unavailable)

    previous = run.recording_state
    run.approval_status = APPROVAL_APPROVED
    run.recording_state = REC_APPROVED
    run.approval_source, run.approval_decided_by = APPROVER_TRAINER, user.id
    run.approval_decided_at = utcnow()
    run.consent_text_version = consent_version
    _log_event(db, run, "approval_granted", from_state=previous, to_state=REC_APPROVED, actor=user)
    # Same transaction as the approval: the job exists if and only if the
    # approval was committed.
    job_queue.enqueue(db, "recording.start", f"recording.start:{run.id}", {"run_id": run.id})
    write_audit_event(db, AuthEventType.recording_approval_granted, ip, ua, user=user,
                      detail="Recording approved by the class trainer", module="recordings",
                      target=f"run:{run.id}", status="success", commit=False)
    db.commit()
    return run, True
