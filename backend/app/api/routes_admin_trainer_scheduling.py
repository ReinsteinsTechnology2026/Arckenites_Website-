from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core import scheduling as sch
from app.core import scheduling_service as svc
from app.core.deps import require_permission
from app.core.notify import notify_user
from app.crud.audit import write_audit_event
from app.database import get_db
from app.models.audit_log import AuthEventType
from app.models.batch import Batch, BatchStatusEnum
from app.models.program import Program, ProgramStatusEnum
from app.models.trainer_scheduling import BatchTrainerAssignment, SchedulingNotice, TrainerExpertise
from app.models.user import RoleEnum, User
from app.schemas.trainer_scheduling import (
    AssignRequest, AssignResult, AvailabilityRequestCreate, BatchListOut, CalendarOut, ExpertiseUpdate, HistoryListOut,
    NoticeOut, TrainerListOut,
)

router = APIRouter(prefix="/admin/trainer-scheduling", tags=["admin-trainer-scheduling"])

view = require_permission("scheduling.view")
manage = require_permission("scheduling.manage")

MAX_COMPARE = 6


def _meta(request: Request) -> tuple[str, str]:
    return (request.client.host if request.client else "unknown", request.headers.get("user-agent", "unknown"))


def _trainer_or_404(db: Session, trainer_id: int) -> User:
    t = db.get(User, trainer_id)
    if t is None or t.role != RoleEnum.staff:
        raise svc.SchedulingError(404, "Trainer not found.", "not_found")
    return t


def _default_period(start: date | None, end: date | None) -> tuple[date, date]:
    if start is None:
        t = datetime.now(sch.UTC).astimezone(sch.business_tz()).date()
        start = t - timedelta(days=t.weekday())
    return start, end or start + timedelta(days=6)


# --------------------------------------------------------------- trainers

@router.get("/trainers", response_model=TrainerListOut)
def list_trainers(
    search: str | None = Query(default=None, max_length=100),
    program_id: int | None = None,
    start: date | None = None,
    end: date | None = None,
    slot_date: date | None = None,
    slot_start: time | None = None,
    slot_end: time | None = None,
    availability: str = Query(default="any", pattern="^(any|free|booked|unavailable|not_declared)$"),
    min_free_hours: float | None = Query(default=None, ge=0),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
    _actor: User = Depends(view),
):
    start, end = _default_period(start, end)
    svc.validate_range(start, end, 93)
    if availability != "any" and not (slot_date and slot_start and slot_end):
        raise svc.SchedulingError(422, "Choose a date and time range to filter by availability.", "slot_required")
    if slot_start and slot_end and slot_end <= slot_start:
        raise svc.SchedulingError(422, "Slot end must be after slot start.", "invalid_range")

    q = select(User).where(User.role == RoleEnum.staff, User.is_active.is_(True))
    if search:
        q = q.where(User.full_name.ilike(f"%{search.strip()}%"))
    if program_id is not None:
        q = q.where(User.id.in_(select(TrainerExpertise.trainer_id).where(TrainerExpertise.program_id == program_id)))
    trainers = db.scalars(q.order_by(User.full_name)).all()
    ids = [t.id for t in trainers]
    cals = sch.load_calendars(db, ids)
    programs: dict[int, list[dict]] = {i: [] for i in ids}
    if ids:
        for tid, pid, name in db.execute(select(TrainerExpertise.trainer_id, Program.id, Program.name)
                                         .join(Program, Program.id == TrainerExpertise.program_id)
                                         .where(TrainerExpertise.trainer_id.in_(ids)).order_by(Program.name)):
            programs[tid].append({"id": pid, "name": name})

    zone = sch.business_tz()
    lo, hi = sch.day_bounds_utc(start, zone)[0], sch.day_bounds_utc(end, zone)[1]
    slot = (sch.local_to_utc(slot_date, slot_start, zone), sch.local_to_utc(slot_date, slot_end, zone)) if slot_date and slot_start and slot_end else None

    rows = []
    for t in trainers:
        cal = cals[t.id]
        bookable = cal.bookable(lo, hi, zone)
        booked = sch.merge([o.interval for o in cal.occurrences(lo, hi, zone=zone)])
        row = {"id": t.id, "full_name": t.full_name, "photo_url": t.photo_url, "programs": programs[t.id],
               "batch_count": len(cal.batches), "assigned_hours": sch.hours(booked), "available_hours": sch.hours(bookable),
               "free_hours": sch.hours(sch.subtract(bookable, booked)),
               "has_availability": bool(cal.rules) or any(e.kind in ("available", "preferred") for e in cal.entries),
               "slot_status": None}
        if slot:
            s_lo, s_hi = slot
            if sch.overlap_len(slot, cal.blocked(s_lo, s_hi)):
                row["slot_status"] = "unavailable"
            elif sch.overlap_len(slot, sch.merge([o.interval for o in cal.occurrences(s_lo, s_hi, zone=zone)])):
                row["slot_status"] = "booked"
            elif sch.is_covered(slot, cal.bookable(s_lo, s_hi, zone)):
                row["slot_status"] = "free"
            else:
                row["slot_status"] = "not_declared"
        rows.append(row)

    if availability != "any":
        rows = [r for r in rows if r["slot_status"] == availability]
    if min_free_hours is not None:
        rows = [r for r in rows if r["free_hours"] >= min_free_hours]
    total = len(rows)
    items = rows[(page - 1) * page_size: page * page_size]
    return {"items": items, "total": total, "page": page, "page_size": page_size, "period_start": start, "period_end": end,
            "timezone": str(zone)}


@router.get("/trainers/{trainer_id}/calendar", response_model=CalendarOut)
def trainer_calendar(trainer_id: int, start: date | None = None, end: date | None = None, db: Session = Depends(get_db),
                     _actor: User = Depends(view)):
    trainer = _trainer_or_404(db, trainer_id)
    start, end = _default_period(start, end)
    svc.validate_range(start, end)
    return svc.build_calendar(db, trainer, start, end)


@router.get("/calendar", response_model=list[CalendarOut])
def compare_calendars(trainer_ids: str = Query(max_length=100), start: date | None = None, end: date | None = None,
                      db: Session = Depends(get_db), _actor: User = Depends(view)):
    try:
        ids = sorted({int(i) for i in trainer_ids.split(",") if i.strip()})
    except ValueError:
        raise svc.SchedulingError(422, "trainer_ids must be comma-separated numbers.", "invalid_ids")
    if not ids or len(ids) > MAX_COMPARE:
        raise svc.SchedulingError(422, f"Compare between 1 and {MAX_COMPARE} trainers.", "invalid_ids")
    start, end = _default_period(start, end)
    svc.validate_range(start, end, 35)
    trainers = db.scalars(select(User).where(User.id.in_(ids), User.role == RoleEnum.staff).order_by(User.full_name)).all()
    if len(trainers) != len(ids):
        raise svc.SchedulingError(404, "One or more trainers were not found.", "not_found")
    return [svc.build_calendar(db, t, start, end) for t in trainers]


# -------------------------------------------------------------- expertise

@router.get("/expertise-options")
def expertise_options(db: Session = Depends(get_db), _actor: User = Depends(view)):
    rows = db.execute(select(Program.id, Program.name).where(Program.status == ProgramStatusEnum.active).order_by(Program.name)).all()
    return [{"id": i, "name": n} for i, n in rows]


@router.get("/trainers/{trainer_id}/expertise")
def get_expertise(trainer_id: int, db: Session = Depends(get_db), _actor: User = Depends(view)):
    _trainer_or_404(db, trainer_id)
    return {"program_ids": sorted(sch.expertise_for(db, [trainer_id])[trainer_id])}


@router.post("/trainers/{trainer_id}/expertise")  # POST (replace-set): the CORS policy only allows GET/POST/PATCH/DELETE
def set_expertise(trainer_id: int, payload: ExpertiseUpdate, request: Request, db: Session = Depends(get_db),
                  actor: User = Depends(manage)):
    trainer = _trainer_or_404(db, trainer_id)
    wanted = set(payload.program_ids)
    valid = set(db.scalars(select(Program.id).where(Program.id.in_(wanted))).all()) if wanted else set()
    if wanted - valid:
        raise svc.SchedulingError(422, "One or more programs do not exist.", "invalid_program")
    sch.lock_trainers(db, [trainer_id])
    current = sch.expertise_for(db, [trainer_id])[trainer_id]
    for pid in wanted - current:
        db.add(TrainerExpertise(trainer_id=trainer_id, program_id=pid))
    if current - wanted:
        for row in db.scalars(select(TrainerExpertise).where(TrainerExpertise.trainer_id == trainer_id,
                                                              TrainerExpertise.program_id.in_(current - wanted))):
            db.delete(row)
    ip, ua = _meta(request)
    write_audit_event(db, AuthEventType.trainer_expertise_changed, ip, ua, user=actor, module="scheduling",
                      target=f"trainer:{trainer_id}", status="success", commit=False,
                      detail=f"Set expertise for {trainer.full_name}: programs {sorted(wanted)}")
    db.commit()
    return {"program_ids": sorted(wanted)}


# ---------------------------------------------------------------- batches

def _batch_row(b: Batch) -> dict:
    return {"id": b.id, "name": b.name, "status": b.status.value, "program_id": b.program_id,
            "program_name": b.program.name if b.program else None,
            "class_days": [d for d in (b.class_days or "").split(",") if d],
            "start_time": sch.hhmm(b.start_time) if b.start_time else None,
            "end_time": sch.hhmm(b.end_time) if b.end_time else None, "start_date": b.start_date, "end_date": b.end_date,
            "trainer_id": b.trainer_id, "trainer_name": b.trainer.full_name if b.trainer else None}


@router.get("/batches", response_model=BatchListOut)
def list_batches(search: str | None = Query(default=None, max_length=100), needs_trainer: bool = False,
                 include_closed: bool = False, page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=50),
                 db: Session = Depends(get_db), _actor: User = Depends(view)):
    q = select(Batch)
    if search:
        q = q.where(Batch.name.ilike(f"%{search.strip()}%"))
    if needs_trainer:
        q = q.where(Batch.trainer_id.is_(None))
    if not include_closed:
        q = q.where(Batch.status.in_((BatchStatusEnum.upcoming, BatchStatusEnum.active, BatchStatusEnum.paused)))
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(q.options(selectinload(Batch.trainer), selectinload(Batch.program))
                      .order_by(Batch.trainer_id.is_(None).desc(), Batch.start_date.asc().nulls_last(), Batch.id)
                      .limit(page_size).offset((page - 1) * page_size)).all()
    return {"items": [_batch_row(b) for b in rows], "total": total, "page": page, "page_size": page_size}


@router.get("/batches/{batch_id}/recommendations")
def batch_recommendations(batch_id: int, db: Session = Depends(get_db), _actor: User = Depends(view)):
    """Read-only ranking. The single side effect is an idempotent admin notice
    (deduplicated per batch) when nobody is suitable; nothing is ever assigned here."""
    batch = db.scalar(select(Batch).where(Batch.id == batch_id).options(selectinload(Batch.sessions), selectinload(Batch.program)))
    if batch is None:
        raise svc.SchedulingError(404, "Batch not found.", "not_found")
    result = sch.recommend(db, batch)
    result["batch"] = {"id": batch.id, "name": batch.name, "program_name": batch.program.name if batch.program else None,
                       "trainer_id": batch.trainer_id}
    if not result["suitable"] and batch.status in (BatchStatusEnum.upcoming, BatchStatusEnum.active):
        n = svc.add_notice(db, kind="no_suitable_trainer", batch_id=batch.id, dedupe=True,
                           message=f"No suitable trainer is available for batch {batch.name}. Review the conflicts or alternative slots.")
        if n is not None:
            db.commit()
    return result


@router.post("/batches/{batch_id}/assign", response_model=AssignResult)
def assign(batch_id: int, payload: AssignRequest, request: Request, db: Session = Depends(get_db), actor: User = Depends(manage)):
    ip, ua = _meta(request)
    return svc.assign_trainer(db, batch_id=batch_id, trainer_id=payload.trainer_id, actor=actor,
                              idempotency_key=payload.idempotency_key, override=payload.override, reason=payload.reason,
                              ip=ip, ua=ua)


# ---------------------------------------------------------------- history

@router.get("/history", response_model=HistoryListOut)
def history(batch_id: int | None = None, trainer_id: int | None = None, page: int = Query(default=1, ge=1),
            page_size: int = Query(default=20, ge=1, le=50), db: Session = Depends(get_db), _actor: User = Depends(view)):
    q = select(BatchTrainerAssignment)
    if batch_id is not None:
        q = q.where(BatchTrainerAssignment.batch_id == batch_id)
    if trainer_id is not None:
        q = q.where(or_(BatchTrainerAssignment.to_trainer_id == trainer_id, BatchTrainerAssignment.from_trainer_id == trainer_id))
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(q.order_by(BatchTrainerAssignment.created_at.desc(), BatchTrainerAssignment.id.desc())
                      .limit(page_size).offset((page - 1) * page_size)).all()
    user_ids = {i for r in rows for i in (r.from_trainer_id, r.to_trainer_id, r.actor_id) if i}
    names = dict(db.execute(select(User.id, User.full_name).where(User.id.in_(user_ids))).all()) if user_ids else {}
    batch_names = dict(db.execute(select(Batch.id, Batch.name).where(Batch.id.in_({r.batch_id for r in rows}))).all()) if rows else {}
    items = [{"id": r.id, "batch_id": r.batch_id, "batch_name": batch_names.get(r.batch_id), "action": r.action,
              "from_trainer": names.get(r.from_trainer_id), "to_trainer": names.get(r.to_trainer_id),
              "actor": names.get(r.actor_id), "was_override": r.was_override, "reason": r.reason,
              "overridden_conflicts": r.overridden_conflicts, "created_at": r.created_at} for r in rows]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


# ----------------------------------------------- notices / availability requests

@router.post("/trainers/{trainer_id}/availability-requests", status_code=201)
def request_availability_change(trainer_id: int, payload: AvailabilityRequestCreate, request: Request,
                                db: Session = Depends(get_db), actor: User = Depends(manage)):
    trainer = _trainer_or_404(db, trainer_id)
    end = payload.end_date or payload.start_date
    svc.validate_range(payload.start_date, end, 366)
    zone = sch.business_tz()
    starts, ends = sch.day_bounds_utc(payload.start_date, zone)[0], sch.day_bounds_utc(end, zone)[1]
    msg = f"An administrator asked you to update your availability for {payload.start_date}" \
          f"{'' if end == payload.start_date else f' to {end}'}: {payload.message.strip()}"
    n = svc.add_notice(db, kind="availability_request", message=msg, recipient_id=trainer.id, trainer_id=trainer.id,
                       starts_at=starts, ends_at=ends, created_by_id=actor.id)
    ip, ua = _meta(request)
    write_audit_event(db, AuthEventType.trainer_availability_changed, ip, ua, user=actor, module="scheduling",
                      target=f"trainer:{trainer.id}", status="success", commit=False,
                      detail=f"Requested availability update from {trainer.full_name} for {payload.start_date} to {end}")
    db.commit()
    notify_user(trainer, "Availability update requested", msg + "\n\nOpen My Availability in the trainer portal to respond: https://arckenites.com")
    return {"id": n.id}


def _notice_rows(db: Session, rows: list[SchedulingNotice]) -> list[dict]:
    uids = {n.trainer_id for n in rows if n.trainer_id}
    bids = {n.batch_id for n in rows if n.batch_id}
    unames = dict(db.execute(select(User.id, User.full_name).where(User.id.in_(uids))).all()) if uids else {}
    bnames = dict(db.execute(select(Batch.id, Batch.name).where(Batch.id.in_(bids))).all()) if bids else {}
    return [{"id": n.id, "kind": n.kind, "message": n.message, "trainer_id": n.trainer_id, "trainer_name": unames.get(n.trainer_id),
             "batch_id": n.batch_id, "batch_name": bnames.get(n.batch_id), "starts_at": n.starts_at, "ends_at": n.ends_at,
             "status": n.status, "created_at": n.created_at} for n in rows]


@router.get("/notices", response_model=list[NoticeOut])
def admin_notices(status: str = Query(default="open", pattern="^(open|resolved)$"), db: Session = Depends(get_db),
                  _actor: User = Depends(view)):
    rows = db.scalars(select(SchedulingNotice).where(SchedulingNotice.recipient_id.is_(None), SchedulingNotice.status == status)
                      .order_by(SchedulingNotice.created_at.desc()).limit(50)).all()
    return _notice_rows(db, list(rows))


@router.post("/notices/{notice_id}/resolve", response_model=NoticeOut)
def resolve_admin_notice(notice_id: int, db: Session = Depends(get_db), _actor: User = Depends(manage)):
    n = db.get(SchedulingNotice, notice_id)
    if n is None or n.recipient_id is not None:
        raise svc.SchedulingError(404, "Notice not found.", "not_found")
    if n.status != "resolved":
        n.status, n.resolved_at = "resolved", datetime.now(sch.UTC)
        db.commit()
    return _notice_rows(db, [n])[0]
