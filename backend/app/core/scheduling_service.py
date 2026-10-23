"""Write-side of trainer scheduling: calendar payloads, availability mutations,
batch-trainer assignment, notices and notifications. Routes stay thin and call
into here; the rules themselves live in app.core.scheduling."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core import scheduling as sch
from app.core.notify import notify_batch_update, notify_user
from app.crud.audit import write_audit_event
from app.crud.permissions import user_has_permission
from app.models.audit_log import AuthEventType
from app.models.batch import Batch, BatchStatusEnum
from app.models.trainer_scheduling import BatchTrainerAssignment, SchedulingNotice
from app.models.user import RoleEnum, User
from app.schemas.trainer_scheduling import AssignResult

MAX_RANGE_DAYS = 62


class SchedulingError(Exception):
    """Business-rule failure rendered as {"detail": message, "code": ..., **extra} (see main.py)."""

    def __init__(self, status_code: int, message: str, code: str = "scheduling_error", **extra):
        super().__init__(message)
        self.status_code, self.message, self.code, self.extra = status_code, message, code, extra


def validate_range(start: date, end: date, max_days: int = MAX_RANGE_DAYS) -> None:
    if end < start:
        raise SchedulingError(422, "End date must not be before start date.", "invalid_range")
    if (end - start).days + 1 > max_days:
        raise SchedulingError(422, f"Date range is limited to {max_days} days.", "range_too_large")


# ------------------------------------------------------------------ calendar

def build_calendar(db: Session, trainer: User, start: date, end: date, now: datetime | None = None) -> dict:
    now = now or datetime.now(sch.UTC)
    zone = sch.business_tz()
    lo, hi = sch.day_bounds_utc(start, zone)[0], sch.day_bounds_utc(end, zone)[1]
    cal = sch.load_calendars(db, [trainer.id])[trainer.id]
    segments: list[dict] = []

    def add(iv, state, label=None, ref=None):
        for d, st, en in sch.split_by_local_day(iv, zone):
            segments.append({"date": d, "start_time": st, "end_time": en, "state": state, "label": label, "ref_id": ref})

    blocked_merged = cal.blocked(lo, hi)
    preferred = cal.preferred(lo, hi, zone)
    bookable = cal.bookable(lo, hi, zone)
    for iv in sch.subtract(bookable, preferred):
        add(iv, "available")
    for iv in preferred:
        add(iv, "preferred")
    for e in cal.entries:
        if e.kind in ("unavailable", "leave") and e.ends_at > lo and e.starts_at < hi:
            for iv in sch.clip([(e.starts_at, e.ends_at)], lo, hi):
                add(iv, e.kind, e.category or e.kind, e.id)
    occs = cal.occurrences(lo, hi, zone=zone)
    for o in occs:
        add((max(o.start, lo), min(o.end, hi)), "booked", o.title, o.batch_id)
    for n in db.scalars(select(SchedulingNotice).where(
            SchedulingNotice.trainer_id == trainer.id, SchedulingNotice.kind == "availability_request",
            SchedulingNotice.status == "open", SchedulingNotice.starts_at < hi, SchedulingNotice.ends_at > lo)):
        add((max(n.starts_at, lo), min(n.ends_at, hi)), "requested", "Admin requested availability", n.id)
    segments.sort(key=lambda s: (s["date"], s["start_time"], s["state"]))

    upcoming_hi = now + timedelta(days=60)
    upcoming = [o for o in cal.occurrences(now, upcoming_hi, zone=zone) if o.end > now][:5]
    booked_hours = sch.hours(sch.clip(sch.merge([o.interval for o in occs]), lo, hi))
    free_hours = sch.hours(sch.subtract(bookable, sch.merge([o.interval for o in occs])))
    rules = sorted(cal.rules, key=lambda r: (r.weekday, r.start_time))
    return {
        "trainer_id": trainer.id, "trainer_name": trainer.full_name, "timezone": str(zone), "start": start, "end": end,
        "segments": segments,
        "rules": [{"id": r.id, "weekday": r.weekday, "start_time": sch.hhmm(r.start_time), "end_time": sch.hhmm(r.end_time),
                   "kind": r.kind, "effective_from": r.effective_from, "effective_until": r.effective_until} for r in rules],
        "entries": [entry_out(e, zone) for e in sorted(cal.entries, key=lambda e: e.starts_at)
                    if e.ends_at > lo and e.starts_at < hi],
        "summary": {"available_hours": sch.hours(bookable), "booked_hours": booked_hours, "free_hours": free_hours,
                    "preferred_hours": sch.hours(preferred),
                    "upcoming_sessions": [_session_row(o, zone) for o in upcoming]},
    }


def entry_out(e, zone) -> dict:
    s, en = e.starts_at.astimezone(zone), e.ends_at.astimezone(zone)
    return {"id": e.id, "kind": e.kind, "category": e.category, "note": e.note, "start_date": s.date(),
            "start_time": sch.hhmm(s.time()), "end_date": en.date(), "end_time": sch.hhmm(en.time()),
            "starts_at": e.starts_at, "ends_at": e.ends_at}


def _session_row(o: sch.Occurrence, zone) -> dict:
    s = o.start.astimezone(zone)
    return {"batch_id": o.batch_id, "batch_name": o.batch_name, "title": o.title, "date": s.date().isoformat(),
            "start_time": sch.hhmm(s.time()), "end_time": sch.hhmm(o.end.astimezone(zone).time())}


# ------------------------------------------------------------------- notices

def add_notice(db: Session, *, kind: str, message: str, recipient_id: int | None = None, trainer_id: int | None = None,
               batch_id: int | None = None, starts_at=None, ends_at=None, created_by_id: int | None = None,
               dedupe: bool = False) -> SchedulingNotice | None:
    if dedupe:
        q = select(SchedulingNotice.id).where(SchedulingNotice.kind == kind, SchedulingNotice.status == "open",
                                              SchedulingNotice.recipient_id.is_(recipient_id) if recipient_id is None else SchedulingNotice.recipient_id == recipient_id,
                                              SchedulingNotice.batch_id.is_(batch_id) if batch_id is None else SchedulingNotice.batch_id == batch_id,
                                              SchedulingNotice.trainer_id.is_(trainer_id) if trainer_id is None else SchedulingNotice.trainer_id == trainer_id)
        if db.scalar(q) is not None:
            return None
    n = SchedulingNotice(kind=kind, message=message, recipient_id=recipient_id, trainer_id=trainer_id, batch_id=batch_id,
                         starts_at=starts_at, ends_at=ends_at, created_by_id=created_by_id)
    db.add(n)
    return n


def resolve_notices(db: Session, *, batch_id: int, kinds: tuple[str, ...]) -> None:
    now = datetime.now(sch.UTC)
    for n in db.scalars(select(SchedulingNotice).where(SchedulingNotice.batch_id == batch_id, SchedulingNotice.kind.in_(kinds),
                                                       SchedulingNotice.status == "open")):
        n.status, n.resolved_at = "resolved", now


# ---------------------------------------------------- availability mutations

def guard_confirmed_batches(db: Session, trainer: User, before: set, *, submit_for_review: bool, review_note: str | None,
                            summary: str, now: datetime | None = None) -> dict | None:
    """Call after the mutation has been flushed. If it newly uncovers a confirmed
    batch session, undo it (rollback) and either raise a 409 warning or — when the
    trainer asked for review — record an admin notice and return the review result."""
    now = now or datetime.now(sch.UTC)
    cal = sch.load_calendars(db, [trainer.id])[trainer.id]
    newly = sch.uncovered_confirmed_sessions(cal, now) - before
    if not newly:
        return None
    affected = sch.describe_sessions(db, newly)
    db.rollback()
    if not submit_for_review:
        raise SchedulingError(
            409, f"This change would leave {len(newly)} confirmed session(s) without availability. "
                 "Ask an administrator to review it, or keep the availability.",
            "confirmed_batch_conflict", conflicts=affected, can_submit_for_review=True)
    names = ", ".join(sorted({a["batch_name"] for a in affected}))
    add_notice(db, kind="availability_review", trainer_id=trainer.id, created_by_id=trainer.id,
               message=f"{trainer.full_name} asked to change availability ({summary}) which affects confirmed batch(es): {names}."
                       + (f" Note: {review_note}" if review_note else ""))
    db.commit()
    return {"status": "submitted_for_review", "detail": "Your request was sent to administrators. Your calendar is unchanged until it is resolved.",
            "conflicts": affected}


def uncovered_before(db: Session, trainer_id: int, now: datetime | None = None) -> set:
    cal = sch.load_calendars(db, [trainer_id])[trainer_id]
    return sch.uncovered_confirmed_sessions(cal, now or datetime.now(sch.UTC))


# ---------------------------------------------------------------- assignment

def _result(batch: Batch, trainer: User | None, action: str, override: bool, replayed=False, warnings=None) -> AssignResult:
    return AssignResult(batch_id=batch.id, trainer_id=trainer.id if trainer else None,
                        trainer_name=trainer.full_name if trainer else None, action=action,
                        was_override=override, replayed=replayed, warnings=warnings or [])


def assert_assignable(db: Session, batch: Batch, trainer_id: int, *, actor: User | None, override: bool,
                      reason: str | None) -> tuple[sch.Evaluation, bool]:
    """Re-run the full rule set inside the caller's transaction. Returns (evaluation,
    override_used). Raises SchedulingError when the assignment is not allowed.
    Overlap with another confirmed batch can never be overridden — reschedule first."""
    trainer = db.get(User, trainer_id)
    if trainer is None or trainer.role != RoleEnum.staff or not trainer.is_active:
        raise SchedulingError(400, "trainer_id must reference an active trainer.", "invalid_trainer")
    ev = sch.evaluate_for_assignment(db, batch, trainer_id)
    if not ev.conflicts:
        return ev, False
    payload = [c.__dict__ for c in ev.conflicts]
    if ev.hard_blocked:
        raise SchedulingError(409, "Trainer already has an overlapping confirmed session. Reschedule one of the batches first.",
                              "overlap", conflicts=payload, can_override=False)
    can_override = actor is not None and user_has_permission(db, actor, "scheduling.override")
    if not override:
        raise SchedulingError(409, "Trainer is not available for every session of this batch.", "unavailable",
                              conflicts=payload, can_override=can_override)
    if not can_override:
        raise SchedulingError(403, "You do not have permission to override scheduling conflicts.", "override_forbidden", conflicts=payload)
    if not reason or len(reason.strip()) < 10:
        raise SchedulingError(422, "A reason of at least 10 characters is required to override a conflict.", "override_reason_required", conflicts=payload)
    return ev, True


def assign_trainer(db: Session, *, batch_id: int, trainer_id: int | None, actor: User, idempotency_key: str,
                   override: bool, reason: str | None, ip: str, ua: str) -> AssignResult:
    prior = db.scalar(select(BatchTrainerAssignment).where(BatchTrainerAssignment.idempotency_key == idempotency_key))
    if prior is not None:
        return _replay(db, prior, batch_id, trainer_id)

    batch = db.scalar(select(Batch).where(Batch.id == batch_id).with_for_update().options(selectinload(Batch.sessions)))
    if batch is None:
        raise SchedulingError(404, "Batch not found.", "not_found")
    if batch.status in (BatchStatusEnum.completed, BatchStatusEnum.cancelled):
        raise SchedulingError(409, f"A {batch.status.value} batch cannot be reassigned.", "batch_closed")
    old_id = batch.trainer_id
    sch.lock_trainers(db, [old_id, trainer_id])
    # Another request holding the batch lock may have just committed our own key.
    prior = db.scalar(select(BatchTrainerAssignment).where(BatchTrainerAssignment.idempotency_key == idempotency_key))
    if prior is not None:
        return _replay(db, prior, batch_id, trainer_id)
    if trainer_id == old_id:
        raise SchedulingError(409, "That is already the batch's trainer." if trainer_id else "The batch has no trainer to remove.", "no_change")

    ev, override_used = (None, False)
    new_trainer = None
    if trainer_id is not None:
        ev, override_used = assert_assignable(db, batch, trainer_id, actor=actor, override=override, reason=reason)
        new_trainer = db.get(User, trainer_id)
    old_trainer = db.get(User, old_id) if old_id else None

    action = "unassigned" if trainer_id is None else ("assigned" if old_id is None else "changed")
    batch.trainer_id = trainer_id
    row = BatchTrainerAssignment(
        batch_id=batch.id, action=action, from_trainer_id=old_id, to_trainer_id=trainer_id, actor_id=actor.id,
        was_override=override_used, reason=(reason.strip() if reason else None), idempotency_key=idempotency_key,
        overridden_conflicts=[c.__dict__ for c in ev.conflicts] if override_used and ev else None)
    db.add(row)
    event = {"assigned": AuthEventType.batch_trainer_assigned, "changed": AuthEventType.batch_trainer_changed,
             "unassigned": AuthEventType.batch_trainer_unassigned}[action]
    detail = f"{action.capitalize()} trainer for batch {batch.name} (ID {batch.id}): " \
             f"{old_trainer.full_name if old_trainer else 'none'} -> {new_trainer.full_name if new_trainer else 'none'}"
    write_audit_event(db, event, ip, ua, user=actor, detail=detail, module="scheduling", target=f"batch:{batch.id}",
                      status="success", commit=False)
    if override_used:
        write_audit_event(db, AuthEventType.batch_trainer_override, ip, ua, user=actor,
                          detail=f"Override on batch {batch.name} (ID {batch.id}) for {new_trainer.full_name}: {reason.strip()}",
                          module="scheduling", target=f"batch:{batch.id}", status="success", commit=False)
    resolve_notices(db, batch_id=batch.id, kinds=("no_suitable_trainer",))
    _queue_trainer_notices(db, batch, old_trainer, new_trainer, actor, override_used, ev)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        prior = db.scalar(select(BatchTrainerAssignment).where(BatchTrainerAssignment.idempotency_key == idempotency_key))
        if prior is not None:
            return _replay(db, prior, batch_id, trainer_id)
        raise
    _email_trainers(batch, old_trainer, new_trainer, override_used)
    return _result(batch, new_trainer, action, override_used, warnings=ev.warnings if ev else None)


def _replay(db: Session, prior: BatchTrainerAssignment, batch_id: int, trainer_id: int | None) -> AssignResult:
    if prior.batch_id != batch_id or prior.to_trainer_id != trainer_id:
        raise SchedulingError(409, "This idempotency key was already used for a different request.", "idempotency_conflict")
    batch, trainer = db.get(Batch, batch_id), (db.get(User, trainer_id) if trainer_id else None)
    return _result(batch, trainer, prior.action, prior.was_override, replayed=True)


def _queue_trainer_notices(db, batch, old_trainer, new_trainer, actor, override_used, ev) -> None:
    if new_trainer is not None:
        msg = f"You were {'assigned to' if old_trainer is None else 'moved onto'} batch {batch.name}."
        add_notice(db, kind="batch_assigned", message=msg, recipient_id=new_trainer.id, trainer_id=new_trainer.id,
                   batch_id=batch.id, created_by_id=actor.id)
        if override_used and ev is not None:
            add_notice(db, kind="scheduling_conflict", recipient_id=new_trainer.id, trainer_id=new_trainer.id, batch_id=batch.id,
                       created_by_id=actor.id,
                       message=f"Batch {batch.name} was assigned to you with a scheduling conflict approved by an administrator: "
                               + "; ".join(c.message for c in ev.conflicts) + " Please contact an administrator if this is a problem.")
    if old_trainer is not None and (new_trainer is None or new_trainer.id != old_trainer.id):
        add_notice(db, kind="batch_cancelled", message=f"You are no longer assigned to batch {batch.name}.",
                   recipient_id=old_trainer.id, trainer_id=old_trainer.id, batch_id=batch.id, created_by_id=actor.id)


def _email_trainers(batch, old_trainer, new_trainer, override_used) -> None:
    if new_trainer is not None:
        notify_batch_update(new_trainer, batch.name, "You have been assigned to this batch"
                            + (" (with an approved scheduling conflict — please review your calendar)" if override_used else ""))
    if old_trainer is not None and (new_trainer is None or new_trainer.id != old_trainer.id):
        notify_batch_update(old_trainer, batch.name, "You have been unassigned from this batch")


def enforce_legacy_assignment(db: Session, batch: Batch, new_trainer_id: int | None) -> None:
    """Hard rules for the pre-existing batch create/update endpoints, which assign
    trainers without the scheduling workflow: no overlap and no leave/unavailable
    time. Declared-availability and workload are advisory there (they have no
    override field) so existing admin flows keep working; the scheduling page
    enforces the full set."""
    if new_trainer_id is None:
        return
    sch.lock_trainers(db, [new_trainer_id])
    ev = sch.evaluate_for_assignment(db, batch, new_trainer_id)
    blocking = [c for c in ev.conflicts if c.code in ("overlap", "leave")]
    if blocking:
        raise SchedulingError(409, "This trainer cannot take the batch: " + " ".join(c.message for c in blocking)
                              + " Use Trainer Scheduling to review and resolve the conflict.", "scheduling_conflict",
                              conflicts=[c.__dict__ for c in blocking], can_override=False)


def record_legacy_assignment(db: Session, batch: Batch, old_id: int | None, new_id: int | None, actor: User) -> None:
    """History row + trainer notice for a trainer change made through the old batch editor."""
    if old_id == new_id:
        return
    action = "unassigned" if new_id is None else ("assigned" if old_id is None else "changed")
    db.add(BatchTrainerAssignment(batch_id=batch.id, action=action, from_trainer_id=old_id, to_trainer_id=new_id, actor_id=actor.id))
    new_t, old_t = (db.get(User, new_id) if new_id else None), (db.get(User, old_id) if old_id else None)
    _queue_trainer_notices(db, batch, old_t, new_t, actor, False, None)
    if new_id:
        resolve_notices(db, batch_id=batch.id, kinds=("no_suitable_trainer",))


def enforce_session_slot(db: Session, batch: Batch, session_date, start_time, end_time) -> None:
    """Server-side guard for every route that creates or moves a ClassSession.
    The batch's trainer must not be double-booked by another confirmed batch or
    scheduled over leave/unavailable time. The batch's own timetable is excluded
    (a rescheduled class legitimately replaces its pattern slot, and an edited
    session never conflicts with itself). The trainer row lock is held until the
    caller commits, so concurrent requests are serialised and re-read committed state.
    Sessions without both times cannot be checked and are allowed, as before."""
    if batch.trainer_id is None or batch.status not in sch.CONFIRMED_BATCH_STATUSES:
        return
    if session_date is None or start_time is None or end_time is None:
        return
    if end_time <= start_time:
        raise SchedulingError(422, "Class end time must be after its start time.", "invalid_range")
    sch.lock_trainers(db, [batch.trainer_id])
    zone = sch.business_tz()
    iv = (sch.local_to_utc(session_date, start_time, zone), sch.local_to_utc(session_date, end_time, zone))
    cal = sch.load_calendars(db, [batch.trainer_id])[batch.trainer_id]
    clash = next((o for o in cal.occurrences(iv[0], iv[1], exclude_batch_id=batch.id, zone=zone)
                  if o.start < iv[1] and o.end > iv[0]), None)
    if clash is not None:
        raise SchedulingError(409, f"The batch's trainer is already teaching {clash.batch_name} at that time.",
                              "scheduling_conflict", can_override=False)
    if sch.overlap_len(iv, cal.blocked(iv[0], iv[1])):
        raise SchedulingError(409, "The batch's trainer is on leave or unavailable at that time.",
                              "scheduling_conflict", can_override=False)


def notify_trainer_email(trainer: User, subject: str, body: str) -> None:
    notify_user(trainer, subject, body)
