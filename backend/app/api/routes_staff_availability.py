from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import scheduling as sch
from app.core import scheduling_service as svc
from app.core.deps import require_role
from app.crud.audit import write_audit_event
from app.database import get_db
from app.models.audit_log import AuthEventType
from app.models.trainer_scheduling import SchedulingNotice, TrainerAvailabilityEntry, TrainerAvailabilityRule
from app.models.user import User
from app.schemas.trainer_scheduling import (
    CalendarOut, EntryCreate, EntryOut, EntryUpdate, NoticeOut, RuleCreate, RuleOut, RuleUpdate,
)

# Every handler derives the trainer from the authenticated token; there is no
# trainer_id anywhere in this router, so a trainer can only ever touch their own data.
router = APIRouter(prefix="/staff/me/availability", tags=["staff-availability"])

MAX_RULES = 100


def _meta(request: Request) -> tuple[str, str]:
    return (request.client.host if request.client else "unknown", request.headers.get("user-agent", "unknown"))


def _audit(db: Session, request: Request, user: User, detail: str) -> None:
    ip, ua = _meta(request)
    write_audit_event(db, AuthEventType.trainer_availability_changed, ip, ua, user=user, detail=detail,
                      module="scheduling", target=f"trainer:{user.id}", status="success", commit=False)


def _today() -> date:
    return datetime.now(sch.UTC).astimezone(sch.business_tz()).date()


def _week(start: date | None, end: date | None) -> tuple[date, date]:
    if start is None:
        t = _today()
        start = t - timedelta(days=t.weekday())
    return start, end or start + timedelta(days=6)


def _review(opts) -> dict:
    return {"submit_for_review": opts.submit_for_review, "review_note": opts.review_note}


def _review_response(result: dict) -> JSONResponse:
    return JSONResponse(status_code=202, content=result)


@router.get("", response_model=CalendarOut)
def my_calendar(start: date | None = None, end: date | None = None, db: Session = Depends(get_db),
                user: User = Depends(require_role("staff"))):
    start, end = _week(start, end)
    svc.validate_range(start, end)
    return svc.build_calendar(db, user, start, end)


# ---------------------------------------------------------- weekly rules

def _rule_out(r: TrainerAvailabilityRule) -> dict:
    return {"id": r.id, "weekday": r.weekday, "start_time": sch.hhmm(r.start_time), "end_time": sch.hhmm(r.end_time),
            "kind": r.kind, "effective_from": r.effective_from, "effective_until": r.effective_until}


@router.post("/rules", response_model=list[RuleOut], status_code=201)
def create_rules(payload: RuleCreate, request: Request, db: Session = Depends(get_db), user: User = Depends(require_role("staff"))):
    sch.lock_trainers(db, [user.id])
    count = db.scalar(select(func.count()).select_from(TrainerAvailabilityRule).where(TrainerAvailabilityRule.trainer_id == user.id))
    if count + len(payload.weekdays) > MAX_RULES:
        raise svc.SchedulingError(422, f"You can keep at most {MAX_RULES} weekly rules.", "too_many_rules")
    if (payload.effective_until or date.max) < _today():
        raise svc.SchedulingError(422, "A weekly rule cannot end in the past.", "past_rule")
    rows = [TrainerAvailabilityRule(trainer_id=user.id, weekday=w, start_time=payload.start_time, end_time=payload.end_time,
                                    kind=payload.kind, effective_from=payload.effective_from,
                                    effective_until=payload.effective_until) for w in payload.weekdays]
    db.add_all(rows)
    db.flush()
    _audit(db, request, user, f"Added weekly {payload.kind} hours {sch.hhmm(payload.start_time)}-{sch.hhmm(payload.end_time)} "
                              f"for weekdays {payload.weekdays}")
    db.commit()
    return [_rule_out(r) for r in rows]


def _own_rule(db: Session, rule_id: int, user: User) -> TrainerAvailabilityRule:
    r = db.get(TrainerAvailabilityRule, rule_id)
    if r is None or r.trainer_id != user.id:  # same 404 for "not yours" and "missing": no existence leak
        raise svc.SchedulingError(404, "Weekly rule not found.", "not_found")
    return r


@router.patch("/rules/{rule_id}", response_model=None)
def update_rule(rule_id: int, payload: RuleUpdate, request: Request, submit_for_review: bool = False,
                db: Session = Depends(get_db), user: User = Depends(require_role("staff"))):
    sch.lock_trainers(db, [user.id])
    rule = _own_rule(db, rule_id, user)
    today = _today()
    start_t = payload.start_time or rule.start_time
    end_t = payload.end_time or rule.end_time
    if end_t <= start_t:
        raise svc.SchedulingError(422, "End time must be after start time.", "invalid_range")
    until = None if payload.clear_effective_until else (payload.effective_until or rule.effective_until)
    if rule.effective_until is not None and rule.effective_until < today:
        raise svc.SchedulingError(409, "Past weekly rules cannot be changed.", "past_rule")
    before = svc.uncovered_before(db, user.id)
    if rule.effective_from > today:
        rule.start_time, rule.end_time, rule.kind = start_t, end_t, payload.kind or rule.kind
        rule.effective_from = payload.effective_from or rule.effective_from
        rule.effective_until = until
        out = rule
    else:
        # Rule already in force: close it yesterday and start the edited copy today, so past hours keep their history.
        new_from = max(payload.effective_from or today, today)
        out = TrainerAvailabilityRule(trainer_id=user.id, weekday=rule.weekday, start_time=start_t, end_time=end_t,
                                      kind=payload.kind or rule.kind, effective_from=new_from, effective_until=until)
        rule.effective_until = new_from - timedelta(days=1)
        db.add(out)
    if out.effective_until and out.effective_until < out.effective_from:
        raise svc.SchedulingError(422, "effective_until must not be before effective_from.", "invalid_range")
    db.flush()
    review = svc.guard_confirmed_batches(db, user, before, submit_for_review=submit_for_review, review_note=None,
                                         summary="edit weekly hours")
    if review:
        return _review_response(review)
    _audit(db, request, user, f"Edited weekly rule {rule_id}")
    db.commit()
    return _rule_out(out)


@router.delete("/rules/{rule_id}", response_model=None)
def delete_rule(rule_id: int, request: Request, submit_for_review: bool = False, db: Session = Depends(get_db),
                user: User = Depends(require_role("staff"))):
    sch.lock_trainers(db, [user.id])
    rule = _own_rule(db, rule_id, user)
    today = _today()
    before = svc.uncovered_before(db, user.id)
    if rule.effective_from > today:
        db.delete(rule)
    elif rule.effective_until is None or rule.effective_until >= today:
        rule.effective_until = today - timedelta(days=1)  # keep history; stop applying from today
    else:
        raise svc.SchedulingError(409, "Past weekly rules cannot be removed.", "past_rule")
    db.flush()
    review = svc.guard_confirmed_batches(db, user, before, submit_for_review=submit_for_review, review_note=None,
                                         summary="remove weekly hours")
    if review:
        return _review_response(review)
    _audit(db, request, user, f"Removed weekly rule {rule_id}")
    db.commit()
    return JSONResponse(status_code=200, content={"status": "removed"})


# ------------------------------------------------------- dated entries

def _bounds(start_date: date, start_time, end_date: date | None, end_time) -> tuple[datetime, datetime]:
    zone = sch.business_tz()
    s = sch.local_to_utc(start_date, start_time, zone)
    e = sch.local_to_utc(end_date or start_date, end_time, zone)
    if e <= s:
        raise svc.SchedulingError(422, "End must be after start.", "invalid_range")
    if e - s > timedelta(days=366):
        raise svc.SchedulingError(422, "An entry cannot be longer than a year.", "range_too_large")
    return s, e


def _check_future(ends_at: datetime) -> None:
    if ends_at <= datetime.now(sch.UTC):
        raise svc.SchedulingError(409, "Availability that has already passed cannot be created or changed.", "past_entry")


@router.post("/entries", response_model=EntryOut, status_code=201)
def create_entry(payload: EntryCreate, request: Request, db: Session = Depends(get_db), user: User = Depends(require_role("staff"))):
    starts, ends = _bounds(payload.start_date, payload.start_time, payload.end_date, payload.end_time)
    _check_future(ends)
    sch.lock_trainers(db, [user.id])
    before = svc.uncovered_before(db, user.id)
    entry = TrainerAvailabilityEntry(trainer_id=user.id, kind=payload.kind, category=payload.category,
                                     starts_at=starts, ends_at=ends, note=payload.note, created_by_id=user.id)
    db.add(entry)
    db.flush()
    review = svc.guard_confirmed_batches(db, user, before, summary=f"mark {payload.kind} {payload.start_date}", **_review(payload))
    if review:
        return _review_response(review)
    _audit(db, request, user, f"Marked {payload.kind} {starts.isoformat()} to {ends.isoformat()}")
    db.commit()
    return svc.entry_out(entry, sch.business_tz())


def _own_entry(db: Session, entry_id: int, user: User) -> TrainerAvailabilityEntry:
    e = db.get(TrainerAvailabilityEntry, entry_id)
    if e is None or e.trainer_id != user.id:
        raise svc.SchedulingError(404, "Availability entry not found.", "not_found")
    return e


@router.patch("/entries/{entry_id}", response_model=EntryOut)
def update_entry(entry_id: int, payload: EntryUpdate, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require_role("staff"))):
    sch.lock_trainers(db, [user.id])
    entry = _own_entry(db, entry_id, user)
    _check_future(entry.ends_at)
    zone = sch.business_tz()
    cur_s, cur_e = entry.starts_at.astimezone(zone), entry.ends_at.astimezone(zone)
    starts, ends = _bounds(payload.start_date or cur_s.date(), payload.start_time or cur_s.time(),
                           payload.end_date or cur_e.date(), payload.end_time or cur_e.time())
    _check_future(ends)
    before = svc.uncovered_before(db, user.id)
    entry.starts_at, entry.ends_at = starts, ends
    if payload.kind:
        entry.kind = payload.kind
    if payload.category is not None:
        entry.category = payload.category
    if payload.note is not None:
        entry.note = payload.note
    db.flush()
    review = svc.guard_confirmed_batches(db, user, before, summary=f"edit {entry.kind} {starts.date()}", **_review(payload))
    if review:
        return _review_response(review)
    _audit(db, request, user, f"Edited availability entry {entry_id}")
    db.commit()
    return svc.entry_out(entry, zone)


@router.delete("/entries/{entry_id}", response_model=None)
def delete_entry(entry_id: int, request: Request, submit_for_review: bool = False, review_note: str | None = Query(default=None, max_length=500),
                 db: Session = Depends(get_db), user: User = Depends(require_role("staff"))):
    sch.lock_trainers(db, [user.id])
    entry = _own_entry(db, entry_id, user)
    _check_future(entry.ends_at)
    before = svc.uncovered_before(db, user.id)
    db.delete(entry)
    db.flush()
    review = svc.guard_confirmed_batches(db, user, before, submit_for_review=submit_for_review, review_note=review_note,
                                         summary="remove availability")
    if review:
        return _review_response(review)
    _audit(db, request, user, f"Removed availability entry {entry_id}")
    db.commit()
    return JSONResponse(status_code=200, content={"status": "removed"})


# ---------------------------------------------------------------- notices

def _notice_out(n: SchedulingNotice) -> dict:
    return {"id": n.id, "kind": n.kind, "message": n.message, "trainer_id": n.trainer_id, "batch_id": n.batch_id,
            "starts_at": n.starts_at, "ends_at": n.ends_at, "status": n.status, "created_at": n.created_at}


@router.get("/notices", response_model=list[NoticeOut])
def my_notices(status: str = Query(default="open", pattern="^(open|resolved)$"), db: Session = Depends(get_db),
               user: User = Depends(require_role("staff"))):
    rows = db.scalars(select(SchedulingNotice).where(SchedulingNotice.recipient_id == user.id, SchedulingNotice.status == status)
                      .order_by(SchedulingNotice.created_at.desc()).limit(50)).all()
    return [_notice_out(n) for n in rows]


@router.post("/notices/{notice_id}/resolve", response_model=NoticeOut)
def resolve_my_notice(notice_id: int, db: Session = Depends(get_db), user: User = Depends(require_role("staff"))):
    n = db.get(SchedulingNotice, notice_id)
    if n is None or n.recipient_id != user.id:
        raise svc.SchedulingError(404, "Notice not found.", "not_found")
    if n.status != "resolved":
        n.status, n.resolved_at = "resolved", datetime.now(sch.UTC)
        db.commit()
    return _notice_out(n)
