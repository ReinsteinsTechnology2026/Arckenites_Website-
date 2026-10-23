"""Trainer availability + batch allocation engine.

Everything that decides "is this trainer free / can they take this batch"
lives here so the trainer calendar, the admin dashboard, the recommendation
list, the confirm-assignment endpoint and the legacy batch PATCH all apply
exactly the same rules.

Timezone strategy
-----------------
* Batch class times, ClassSession times and recurring availability rules are
  wall-clock values in `settings.scheduling_timezone` (the repo has always
  stored them naive).
* One-off availability entries are stored as UTC instants (timestamptz).
* All comparisons happen on timezone-aware UTC datetimes. Wall-clock values are
  converted with zoneinfo per date, so a DST change moves the UTC instant but
  never the local working day.

Pure functions (intervals, occurrence expansion, evaluation) take plain data;
DB access is confined to the `load_*` helpers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.models.batch import Batch, BatchStatusEnum
from app.models.trainer_scheduling import (
    BLOCKING_KINDS, TrainerAvailabilityEntry, TrainerAvailabilityRule, TrainerExpertise,
)
from app.models.user import RoleEnum, User

UTC = timezone.utc
CONFIRMED_BATCH_STATUSES = (BatchStatusEnum.upcoming, BatchStatusEnum.active)
HORIZON_DAYS = 120  # how far an open-ended (no end_date) batch pattern is expanded
DAY_INDEX = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}

Interval = tuple[datetime, datetime]


def business_tz() -> ZoneInfo:
    return ZoneInfo(settings.scheduling_timezone)


def local_to_utc(d: date, t: time, zone: ZoneInfo | None = None) -> datetime:
    """Wall-clock -> UTC instant. A time skipped by a DST gap resolves forward;
    an ambiguous (repeated) time resolves to its first occurrence."""
    return datetime.combine(d, t).replace(tzinfo=zone or business_tz()).astimezone(UTC)


def day_bounds_utc(d: date, zone: ZoneInfo | None = None) -> Interval:
    zone = zone or business_tz()
    return local_to_utc(d, time.min, zone), local_to_utc(d + timedelta(days=1), time.min, zone)


def hhmm(t: time) -> str:
    return t.strftime("%H:%M")


# ---------------------------------------------------------------- intervals

def merge(intervals: list[Interval]) -> list[Interval]:
    out: list[Interval] = []
    for s, e in sorted(i for i in intervals if i[1] > i[0]):
        if out and s <= out[-1][1]:
            if e > out[-1][1]:
                out[-1] = (out[-1][0], e)
        else:
            out.append((s, e))
    return out


def subtract(base: list[Interval], cut: list[Interval]) -> list[Interval]:
    """base - cut; both must already be merged."""
    out: list[Interval] = []
    for s, e in base:
        cur = s
        for cs, ce in cut:
            if ce <= cur or cs >= e:
                continue
            if cs > cur:
                out.append((cur, cs))
            cur = max(cur, ce)
            if cur >= e:
                break
        if cur < e:
            out.append((cur, e))
    return out


def clip(intervals: list[Interval], lo: datetime, hi: datetime) -> list[Interval]:
    return [(max(s, lo), min(e, hi)) for s, e in intervals if e > lo and s < hi]


def is_covered(iv: Interval, merged: list[Interval]) -> bool:
    return any(s <= iv[0] and e >= iv[1] for s, e in merged)


def overlap_len(iv: Interval, merged: list[Interval]) -> timedelta:
    total = timedelta(0)
    for s, e in merged:
        lo, hi = max(s, iv[0]), min(e, iv[1])
        if hi > lo:
            total += hi - lo
    return total


def hours(intervals: list[Interval]) -> float:
    return round(sum((e - s).total_seconds() for s, e in intervals) / 3600, 2)


def split_by_local_day(iv: Interval, zone: ZoneInfo | None = None):
    """Yield (local_date, 'HH:MM', 'HH:MM') pieces; a piece ending at local midnight ends '24:00'."""
    zone = zone or business_tz()
    cur, end = iv
    while cur < end:
        local = cur.astimezone(zone)
        nxt = local_to_utc(local.date() + timedelta(days=1), time.min, zone)
        seg_end = min(end, nxt)
        end_label = "24:00" if seg_end == nxt else hhmm(seg_end.astimezone(zone).time())
        yield local.date(), hhmm(local.time()), end_label
        cur = seg_end


# --------------------------------------------------------------- occurrences

@dataclass(frozen=True)
class Occurrence:
    batch_id: int
    batch_name: str
    title: str
    start: datetime
    end: datetime
    source: str  # "pattern" | "session"

    @property
    def interval(self) -> Interval:
        return (self.start, self.end)


def _class_day_indexes(class_days: str | None) -> set[int]:
    return {DAY_INDEX[d.strip()[:3].lower()] for d in (class_days or "").split(",") if d.strip()[:3].lower() in DAY_INDEX}


def batch_occurrences(batch: Batch, win_start: datetime, win_end: datetime, zone: ZoneInfo | None = None) -> list[Occurrence]:
    """Concrete class occurrences of a batch inside [win_start, win_end): its
    weekly pattern (class_days + start/end time between start/end date) plus any
    explicit ClassSession rows. Sessions with no time can't be checked and are skipped."""
    zone = zone or business_tz()
    found: dict[tuple[datetime, datetime], Occurrence] = {}

    for s in batch.sessions:
        if s.start_time and s.end_time and s.end_time > s.start_time:
            st, en = local_to_utc(s.session_date, s.start_time, zone), local_to_utc(s.session_date, s.end_time, zone)
            if en > win_start and st < win_end:
                found[(st, en)] = Occurrence(batch.id, batch.name, s.title, st, en, "session")

    days = _class_day_indexes(batch.class_days)
    if days and batch.start_time and batch.end_time and batch.end_time > batch.start_time and batch.start_date:
        first = max(batch.start_date, (win_start.astimezone(zone) - timedelta(days=1)).date())
        last_cap = (win_end.astimezone(zone) + timedelta(days=1)).date()
        last = min(batch.end_date, last_cap) if batch.end_date else last_cap
        d = first
        while d <= last:
            if d.weekday() in days:
                st, en = local_to_utc(d, batch.start_time, zone), local_to_utc(d, batch.end_time, zone)
                if en > win_start and st < win_end and (st, en) not in found:
                    found[(st, en)] = Occurrence(batch.id, batch.name, batch.name, st, en, "pattern")
            d += timedelta(days=1)
    return sorted(found.values(), key=lambda o: o.start)


def batch_required_window(batch: Batch, now: datetime, zone: ZoneInfo | None = None) -> Interval:
    zone = zone or business_tz()
    today = now.astimezone(zone).date()
    start = max(batch.start_date, today) if batch.start_date else today
    end = batch.end_date if batch.end_date else start + timedelta(days=HORIZON_DAYS)
    end = min(end, start + timedelta(days=366))
    lo = local_to_utc(start, time.min, zone)
    return lo, local_to_utc(end + timedelta(days=1), time.min, zone)


def required_occurrences(batch: Batch, now: datetime, zone: ZoneInfo | None = None) -> list[Occurrence]:
    """Sessions of `batch` that still lie in the future — the ones a trainer must be free for."""
    lo, hi = batch_required_window(batch, now, zone)
    return [o for o in batch_occurrences(batch, lo, hi, zone) if o.end > now]


# ----------------------------------------------------------------- calendars

@dataclass
class TrainerCalendar:
    trainer_id: int
    rules: list[TrainerAvailabilityRule] = field(default_factory=list)
    entries: list[TrainerAvailabilityEntry] = field(default_factory=list)
    batches: list[Batch] = field(default_factory=list)  # confirmed (upcoming/active) batches

    def rule_intervals(self, kind: str, lo: datetime, hi: datetime, zone: ZoneInfo | None = None) -> list[Interval]:
        zone = zone or business_tz()
        out: list[Interval] = []
        d = (lo.astimezone(zone) - timedelta(days=1)).date()
        last = (hi.astimezone(zone) + timedelta(days=1)).date()
        rules = [r for r in self.rules if r.kind == kind]
        while d <= last:
            for r in rules:
                if r.weekday == d.weekday() and r.effective_from <= d and (r.effective_until is None or d <= r.effective_until):
                    out.append((local_to_utc(d, r.start_time, zone), local_to_utc(d, r.end_time, zone)))
            d += timedelta(days=1)
        return clip(out, lo, hi)

    def entry_intervals(self, kinds: tuple[str, ...], lo: datetime, hi: datetime) -> list[Interval]:
        return clip([(e.starts_at, e.ends_at) for e in self.entries if e.kind in kinds], lo, hi)

    def blocked(self, lo: datetime, hi: datetime) -> list[Interval]:
        return merge(self.entry_intervals(BLOCKING_KINDS, lo, hi))

    def preferred(self, lo: datetime, hi: datetime, zone: ZoneInfo | None = None) -> list[Interval]:
        raw = self.rule_intervals("preferred", lo, hi, zone) + self.entry_intervals(("preferred",), lo, hi)
        return subtract(merge(raw), self.blocked(lo, hi))

    def bookable(self, lo: datetime, hi: datetime, zone: ZoneInfo | None = None) -> list[Interval]:
        """Declared available/preferred time minus leave/unavailable exceptions."""
        raw = (self.rule_intervals("available", lo, hi, zone) + self.rule_intervals("preferred", lo, hi, zone)
               + self.entry_intervals(("available", "preferred"), lo, hi))
        return subtract(merge(raw), self.blocked(lo, hi))

    def occurrences(self, lo: datetime, hi: datetime, exclude_batch_id: int | None = None,
                    zone: ZoneInfo | None = None) -> list[Occurrence]:
        out: list[Occurrence] = []
        for b in self.batches:
            if b.id != exclude_batch_id:
                out.extend(batch_occurrences(b, lo, hi, zone))
        return sorted(out, key=lambda o: o.start)

    def free(self, lo: datetime, hi: datetime, zone: ZoneInfo | None = None) -> list[Interval]:
        booked = merge([o.interval for o in self.occurrences(lo, hi, zone=zone)])
        return subtract(self.bookable(lo, hi, zone), booked)


def load_calendars(db: Session, trainer_ids: list[int]) -> dict[int, TrainerCalendar]:
    """Three set-based queries for any number of trainers (no per-trainer lookups)."""
    cals = {tid: TrainerCalendar(tid) for tid in trainer_ids}
    if not cals:
        return cals
    for r in db.scalars(select(TrainerAvailabilityRule).where(TrainerAvailabilityRule.trainer_id.in_(trainer_ids))):
        cals[r.trainer_id].rules.append(r)
    for e in db.scalars(select(TrainerAvailabilityEntry).where(TrainerAvailabilityEntry.trainer_id.in_(trainer_ids))):
        cals[e.trainer_id].entries.append(e)
    batches = db.scalars(
        select(Batch).where(Batch.trainer_id.in_(trainer_ids), Batch.status.in_(CONFIRMED_BATCH_STATUSES))
        .options(selectinload(Batch.sessions))
    ).all()
    for b in batches:
        cals[b.trainer_id].batches.append(b)
    return cals


def lock_trainers(db: Session, trainer_ids: list[int]) -> None:
    """Row-lock trainers in ascending id order. Every assignment / availability
    mutation takes this lock first, so concurrent writers for one trainer are
    serialised and each re-reads committed state (no double-booking)."""
    ids = sorted({i for i in trainer_ids if i is not None})
    if ids:
        db.execute(select(User.id).where(User.id.in_(ids)).order_by(User.id).with_for_update()).all()


# ---------------------------------------------------------------- evaluation

@dataclass
class Conflict:
    code: str            # overlap | leave | outside_availability | workload
    message: str
    overridable: bool
    count: int = 1
    examples: list[dict] = field(default_factory=list)


@dataclass
class Evaluation:
    trainer_id: int
    session_count: int = 0
    required_hours: float = 0.0
    conflicts: list[Conflict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    expertise_matched: bool | None = None  # None = batch has no program, nothing required
    preferred_ratio: float = 0.0
    peak_week_hours: float = 0.0
    schedule_checked: bool = True

    @property
    def eligible(self) -> bool:
        return not self.conflicts and self.expertise_matched is not False

    @property
    def hard_blocked(self) -> bool:
        return any(not c.overridable for c in self.conflicts)


def _example(o: Occurrence, zone: ZoneInfo, extra: str = "") -> dict:
    local = o.start.astimezone(zone)
    return {"batch_id": o.batch_id, "date": local.date().isoformat(),
            "start_time": hhmm(local.time()), "end_time": hhmm(o.end.astimezone(zone).time()), "detail": extra}


def _fmt(o: Occurrence, zone: ZoneInfo) -> str:
    l = o.start.astimezone(zone)
    return f"{l:%a %d %b %Y} {l:%H:%M}-{o.end.astimezone(zone):%H:%M}"


def _weekly_hours(occs: list[Occurrence], zone: ZoneInfo) -> dict[tuple[int, int], float]:
    weeks: dict[tuple[int, int], float] = {}
    for o in occs:
        iso = o.start.astimezone(zone).isocalendar()
        weeks[(iso[0], iso[1])] = weeks.get((iso[0], iso[1]), 0.0) + (o.end - o.start).total_seconds() / 3600
    return weeks


def evaluate(required: list[Occurrence], cal: TrainerCalendar, expertise_program_ids: set[int],
             batch_program_id: int | None, batch_id: int, zone: ZoneInfo | None = None,
             max_weekly_hours: float | None = None) -> Evaluation:
    """Can this trainer take the batch whose future sessions are `required`?"""
    zone = zone or business_tz()
    max_weekly = settings.scheduling_max_weekly_hours if max_weekly_hours is None else max_weekly_hours
    ev = Evaluation(trainer_id=cal.trainer_id)
    if batch_program_id is not None:
        ev.expertise_matched = batch_program_id in expertise_program_ids

    ev.session_count = len(required)
    ev.required_hours = hours(merge([o.interval for o in required]))
    if not required:
        ev.schedule_checked = False
        ev.warnings.append("Batch has no class days/times (or none remaining), so availability could not be checked.")
        return ev

    lo, hi = min(o.start for o in required), max(o.end for o in required)
    others = cal.occurrences(lo, hi, exclude_batch_id=batch_id, zone=zone)
    blocked = cal.blocked(lo, hi)
    bookable = cal.bookable(lo, hi, zone)
    preferred = cal.preferred(lo, hi, zone)

    overlaps: list[dict] = []
    leave: list[dict] = []
    outside: list[dict] = []
    pref_total = timedelta(0)
    total = timedelta(0)
    for o in required:
        total += o.end - o.start
        pref_total += overlap_len(o.interval, preferred)
        clash = next((x for x in others if x.start < o.end and x.end > o.start), None)
        if clash:
            overlaps.append(_example(o, zone, f"overlaps {clash.batch_name}"))
        if overlap_len(o.interval, blocked):
            leave.append(_example(o, zone, "falls in leave/unavailable time"))
        elif not is_covered(o.interval, bookable):
            outside.append(_example(o, zone, "outside declared availability"))

    if overlaps:
        ev.conflicts.append(Conflict("overlap", f"{len(overlaps)} session(s) overlap another confirmed batch.", False, len(overlaps), overlaps[:5]))
    if leave:
        ev.conflicts.append(Conflict("leave", f"{len(leave)} session(s) fall in leave or unavailable time.", True, len(leave), leave[:5]))
    if outside:
        ev.conflicts.append(Conflict("outside_availability", f"{len(outside)} session(s) are outside the trainer's declared availability.", True, len(outside), outside[:5]))

    existing = _weekly_hours(others, zone)
    new = _weekly_hours(required, zone)
    over = {w: round(existing.get(w, 0.0) + h, 2) for w, h in new.items() if existing.get(w, 0.0) + h > max_weekly + 1e-9}
    ev.peak_week_hours = round(max((existing.get(w, 0.0) + h for w, h in new.items()), default=0.0), 2)
    if over:
        ev.conflicts.append(Conflict("workload", f"Would exceed the {max_weekly:g} h/week workload limit in {len(over)} week(s) (peak {max(over.values()):g} h).", True, len(over)))

    ev.preferred_ratio = round(pref_total / total, 3) if total else 0.0
    return ev


def expertise_for(db: Session, trainer_ids: list[int]) -> dict[int, set[int]]:
    out: dict[int, set[int]] = {t: set() for t in trainer_ids}
    if trainer_ids:
        for t, p in db.execute(select(TrainerExpertise.trainer_id, TrainerExpertise.program_id).where(TrainerExpertise.trainer_id.in_(trainer_ids))):
            out[t].add(p)
    return out


def evaluate_for_assignment(db: Session, batch: Batch, trainer_id: int, now: datetime | None = None) -> Evaluation:
    """Authoritative check used by confirm-assignment and the legacy batch PATCH.
    Caller is expected to have called lock_trainers() in the same transaction."""
    now = now or datetime.now(UTC)
    zone = business_tz()
    cal = load_calendars(db, [trainer_id])[trainer_id]
    exp = expertise_for(db, [trainer_id])[trainer_id]
    return evaluate(required_occurrences(batch, now, zone), cal, exp, batch.program_id, batch.id, zone)


# ----------------------------------------------------------- recommendations

SCORE_WEIGHTS = {"expertise": 40, "preferred": 35, "workload": 25}


def score_breakdown(ev: Evaluation, max_weekly_hours: float | None = None) -> list[dict]:
    max_weekly = max_weekly_hours or settings.scheduling_max_weekly_hours
    expertise_pts = SCORE_WEIGHTS["expertise"]
    if ev.expertise_matched is True:
        note = "Trainer teaches this batch's program."
    elif ev.expertise_matched is None:
        note = "Batch has no program, so expertise is not required (full points to everyone)."
    else:
        expertise_pts, note = 0, "Trainer has no recorded expertise for this program."
    pref_pts = round(SCORE_WEIGHTS["preferred"] * ev.preferred_ratio, 1)
    headroom = max(0.0, 1 - (ev.peak_week_hours / max_weekly)) if max_weekly else 0.0
    work_pts = round(SCORE_WEIGHTS["workload"] * headroom, 1)
    return [
        {"criterion": "Course expertise", "points": expertise_pts, "max": SCORE_WEIGHTS["expertise"], "note": note},
        {"criterion": "Preferred hours", "points": pref_pts, "max": SCORE_WEIGHTS["preferred"],
         "note": f"{round(ev.preferred_ratio * 100)}% of session time falls in the trainer's preferred hours."},
        {"criterion": "Workload headroom", "points": work_pts, "max": SCORE_WEIGHTS["workload"],
         "note": f"Busiest week would be {ev.peak_week_hours:g} of {max_weekly:g} h."},
    ]


def alternative_slots(cals: dict[int, TrainerCalendar], names: dict[int, str], duration: timedelta, now: datetime,
                      days: int = 28, per_trainer: int = 3, limit: int = 10) -> list[dict]:
    """Earliest free windows that could fit one session — offered when nobody fits as scheduled."""
    zone = business_tz()
    lo = now
    hi = local_to_utc(now.astimezone(zone).date() + timedelta(days=days), time.min, zone)
    out: list[dict] = []
    for tid, cal in cals.items():
        n = 0
        for s, e in cal.free(lo, hi, zone):
            if e - s >= duration and n < per_trainer:
                for d, st, en in split_by_local_day((s, e), zone):
                    out.append({"trainer_id": tid, "trainer_name": names.get(tid, ""), "date": d.isoformat(), "start_time": st, "end_time": en})
                    break
                n += 1
    out.sort(key=lambda a: (a["date"], a["start_time"]))
    return out[:limit]


def active_trainers(db: Session) -> list[User]:
    return list(db.scalars(select(User).where(User.role == RoleEnum.staff, User.is_active.is_(True)).order_by(User.full_name)))


def recommend(db: Session, batch: Batch, now: datetime | None = None) -> dict:
    """Ranked suitable trainers, excluded trainers with reasons, and (when nobody
    fits) alternative free windows. Never changes anything."""
    now = now or datetime.now(UTC)
    zone = business_tz()
    trainers = active_trainers(db)
    ids = [t.id for t in trainers]
    cals = load_calendars(db, ids)
    exp = expertise_for(db, ids)
    required = required_occurrences(batch, now, zone)
    suitable, excluded = [], []

    for t in trainers:
        ev = evaluate(required, cals[t.id], exp[t.id], batch.program_id, batch.id, zone)
        row = {"trainer_id": t.id, "full_name": t.full_name, "photo_url": t.photo_url, "is_current": batch.trainer_id == t.id,
               "expertise_matched": ev.expertise_matched, "session_count": ev.session_count, "required_hours": ev.required_hours,
               "peak_week_hours": ev.peak_week_hours, "preferred_percent": round(ev.preferred_ratio * 100),
               "schedule_checked": ev.schedule_checked, "warnings": ev.warnings,
               "conflicts": [c.__dict__ for c in ev.conflicts], "overridable": not ev.hard_blocked}
        reasons = [c.message for c in ev.conflicts]
        if ev.expertise_matched is False:
            reasons.append("No recorded expertise for this batch's program.")
        if ev.eligible:
            parts = score_breakdown(ev)
            row["score"] = round(sum(p["points"] for p in parts), 1)
            row["score_breakdown"] = parts
            row["reasons"] = ["Teaches this program." if ev.expertise_matched else "Program expertise not required.",
                              "Free for all remaining sessions." if ev.schedule_checked else "Availability not checked (no schedule)."]
            suitable.append(row)
        else:
            row["reasons"] = reasons
            excluded.append(row)

    # Existing confirmed assignment first, then by score, then name — documented in the UI legend.
    suitable.sort(key=lambda r: (not r["is_current"], -r["score"], r["full_name"].lower()))
    excluded.sort(key=lambda r: (not r["is_current"], r["full_name"].lower()))

    alternatives: list[dict] = []
    if not suitable:
        duration = (batch.end_time and batch.start_time and
                    datetime.combine(date.min, batch.end_time) - datetime.combine(date.min, batch.start_time)) or \
            timedelta(minutes=settings.scheduling_default_session_minutes)
        pool = [t.id for t in trainers if batch.program_id is None or batch.program_id in exp[t.id]] or ids
        alternatives = alternative_slots({i: cals[i] for i in pool}, {t.id: t.full_name for t in trainers}, duration, now)
    return {"batch_id": batch.id, "session_count": len(required), "suitable": suitable, "excluded": excluded,
            "alternatives": alternatives, "weights": SCORE_WEIGHTS, "max_weekly_hours": settings.scheduling_max_weekly_hours}


# ------------------------------------------------ impact of an availability edit

def uncovered_confirmed_sessions(cal: TrainerCalendar, now: datetime, zone: ZoneInfo | None = None) -> set[tuple[int, datetime, datetime]]:
    """Future sessions of the trainer's confirmed batches that are NOT inside bookable time."""
    zone = zone or business_tz()
    out: set[tuple[int, datetime, datetime]] = set()
    for b in cal.batches:
        occs = required_occurrences(b, now, zone)
        if not occs:
            continue
        bookable = cal.bookable(min(o.start for o in occs), max(o.end for o in occs), zone)
        for o in occs:
            if not is_covered(o.interval, bookable):
                out.add((b.id, o.start, o.end))
    return out


def describe_sessions(db: Session, sessions: set[tuple[int, datetime, datetime]], limit: int = 8) -> list[dict]:
    zone = business_tz()
    names = dict(db.execute(select(Batch.id, Batch.name).where(Batch.id.in_({s[0] for s in sessions}))).all()) if sessions else {}
    rows = []
    for bid, s, e in sorted(sessions, key=lambda x: x[1])[:limit]:
        l = s.astimezone(zone)
        rows.append({"batch_id": bid, "batch_name": names.get(bid, ""), "date": l.date().isoformat(),
                     "start_time": hhmm(l.time()), "end_time": hhmm(e.astimezone(zone).time())})
    return rows
