"""Durable background job queue backed by PostgreSQL.

Guarantees, and how each is achieved:

* One execution at a time per job, across any number of worker or API
  processes: a job is claimed with a single compare-and-swap UPDATE whose
  WHERE clause only matches a job that is still claimable. Concurrent
  claimers serialise on the row; exactly one UPDATE matches. (On PostgreSQL
  the candidate SELECT also uses FOR UPDATE SKIP LOCKED to avoid contention.)
* No lost work on crashes: a claim is a lease. If a worker dies, the lease
  expires and the job is claimed again.
* No zombie completions: completing or failing a job is fenced on the worker
  id AND the attempt number that claimed it, so a worker whose lease expired
  (and whose job was re-claimed) cannot mark it done or overwrite its state.
* No duplicate jobs: `dedupe_key` is unique.
* Bounded retries: exponential backoff, then DEAD. A job that keeps expiring
  its lease is also moved to DEAD rather than re-run forever.

Handlers must be idempotent: delivery is at-least-once.
"""
import json
import logging
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models.class_run import JOB_DEAD, JOB_PENDING, JOB_RUNNING, JOB_SUCCEEDED, BackgroundJob

logger = logging.getLogger("job_queue")
UTC = timezone.utc

BACKOFF_BASE_SECONDS = 15
BACKOFF_MAX_SECONDS = 900
MAX_ERROR_LENGTH = 300

Handler = Callable[[Session, BackgroundJob], None]
DeadHandler = Callable[[Session, BackgroundJob], None]

HANDLERS: dict[str, Handler] = {}
DEAD_HANDLERS: dict[str, DeadHandler] = {}


class RetryLater(Exception):
    """A transient failure: try again after `delay_seconds` (default: backoff)."""

    def __init__(self, message: str = "retry", delay_seconds: float | None = None):
        super().__init__(message)
        self.delay_seconds = delay_seconds


class PermanentFailure(Exception):
    """Retrying cannot help; move the job straight to DEAD."""


def register(kind: str, *, on_dead: DeadHandler | None = None):
    def decorator(func: Handler) -> Handler:
        HANDLERS[kind] = func
        if on_dead is not None:
            DEAD_HANDLERS[kind] = on_dead
        return func
    return decorator


def utcnow() -> datetime:
    return datetime.now(UTC)


def backoff_seconds(attempts: int) -> float:
    return float(min(BACKOFF_BASE_SECONDS * (2 ** max(attempts - 1, 0)), BACKOFF_MAX_SECONDS))


def _claimable(now: datetime):
    return or_(
        and_(BackgroundJob.state == JOB_PENDING, BackgroundJob.run_at <= now),
        and_(BackgroundJob.state == JOB_RUNNING, BackgroundJob.lease_until < now),
    )


def enqueue(
    db: Session, kind: str, dedupe_key: str, payload: dict | None = None, *,
    run_at: datetime | None = None, max_attempts: int = 5,
) -> bool:
    """Adds the job unless one with this dedupe key already exists (in any
    state). Returns True only if it created one. Does not commit: it joins the
    caller's transaction, so a job exists if and only if the surrounding change
    committed."""
    if db.scalar(select(BackgroundJob.id).where(BackgroundJob.dedupe_key == dedupe_key)) is not None:
        return False
    try:
        with db.begin_nested():
            db.add(BackgroundJob(
                kind=kind, dedupe_key=dedupe_key, payload=json.dumps(payload or {}), state=JOB_PENDING,
                run_at=run_at or utcnow(), max_attempts=max_attempts, attempts=0,
            ))
        return True
    except IntegrityError:  # lost a race for the same key
        return False


def claim_next(
    db: Session, worker_id: str, *, now: datetime | None = None, lease_seconds: int | None = None,
    kinds: Iterable[str] | None = None,
) -> BackgroundJob | None:
    now = now or utcnow()
    lease = timedelta(seconds=lease_seconds or settings.job_lease_seconds)
    kinds = list(kinds) if kinds is not None else None

    for _ in range(20):
        query = select(BackgroundJob.id).where(_claimable(now)).order_by(BackgroundJob.run_at, BackgroundJob.id).limit(1)
        if kinds is not None:
            query = query.where(BackgroundJob.kind.in_(kinds))
        if db.get_bind().dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
        job_id = db.scalar(query)
        if job_id is None:
            db.rollback()
            return None

        claimed = db.execute(
            update(BackgroundJob)
            .where(BackgroundJob.id == job_id, _claimable(now))
            .values(state=JOB_RUNNING, locked_by=worker_id, lease_until=now + lease,
                    attempts=BackgroundJob.attempts + 1, updated_at=now)
        ).rowcount
        db.commit()
        if claimed != 1:
            continue  # another worker won the race; look for the next candidate

        db.expire_all()
        job = db.get(BackgroundJob, job_id)
        if job.attempts > job.max_attempts:
            # The lease expired on every allowed attempt (the handler keeps
            # crashing or hanging). Do not run it again.
            _mark_dead(db, job, "lease expired on every attempt")
            db.commit()
            continue
        return job
    return None


def _fenced(job: BackgroundJob, worker_id: str):
    return and_(
        BackgroundJob.id == job.id, BackgroundJob.state == JOB_RUNNING,
        BackgroundJob.locked_by == worker_id, BackgroundJob.attempts == job.attempts,
    )


def complete(db: Session, job: BackgroundJob, worker_id: str) -> bool:
    """True if this worker still owned the job and it is now SUCCEEDED."""
    now = utcnow()
    ok = db.execute(
        update(BackgroundJob).where(_fenced(job, worker_id)).values(
            state=JOB_SUCCEEDED, completed_at=now, lease_until=None, locked_by=None, last_error=None, updated_at=now)
    ).rowcount == 1
    return ok


def extend_lease(db: Session, job: BackgroundJob, worker_id: str, seconds: int | None = None) -> bool:
    now = utcnow()
    ok = db.execute(
        update(BackgroundJob).where(_fenced(job, worker_id)).values(
            lease_until=now + timedelta(seconds=seconds or settings.job_lease_seconds), updated_at=now)
    ).rowcount == 1
    db.commit()
    return ok


def _safe_error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:MAX_ERROR_LENGTH]


def _mark_dead(db: Session, job: BackgroundJob, error: str) -> None:
    now = utcnow()
    db.execute(update(BackgroundJob).where(BackgroundJob.id == job.id).values(
        state=JOB_DEAD, completed_at=now, lease_until=None, locked_by=None, last_error=error[:MAX_ERROR_LENGTH], updated_at=now))
    handler = DEAD_HANDLERS.get(job.kind)
    if handler is not None:
        try:
            db.expire_all()
            handler(db, db.get(BackgroundJob, job.id))
        except Exception:  # a dead-letter handler must never wedge the queue
            logger.exception("dead-letter handler failed kind=%s job_id=%s", job.kind, job.id)
            db.rollback()
            db.execute(update(BackgroundJob).where(BackgroundJob.id == job.id).values(
                state=JOB_DEAD, completed_at=now, lease_until=None, locked_by=None,
                last_error=error[:MAX_ERROR_LENGTH], updated_at=now))
    logger.error("job dead kind=%s job_id=%s", job.kind, job.id)


def fail(
    db: Session, job: BackgroundJob, worker_id: str, error: str, *, permanent: bool = False,
    retry_in: float | None = None,
) -> str | None:
    """Records a failed attempt. Returns the new state, or None if this worker
    no longer owned the job (in which case nothing is changed)."""
    now = utcnow()
    if permanent or job.attempts >= job.max_attempts:
        owned = db.execute(update(BackgroundJob).where(_fenced(job, worker_id)).values(
            last_error=error[:MAX_ERROR_LENGTH], updated_at=now)).rowcount == 1
        if not owned:
            return None
        _mark_dead(db, job, error)
        return JOB_DEAD
    delay = retry_in if retry_in is not None else backoff_seconds(job.attempts)
    owned = db.execute(update(BackgroundJob).where(_fenced(job, worker_id)).values(
        state=JOB_PENDING, run_at=now + timedelta(seconds=delay), locked_by=None, lease_until=None,
        last_error=error[:MAX_ERROR_LENGTH], updated_at=now)).rowcount == 1
    return JOB_PENDING if owned else None


def run_one(session_factory, worker_id: str, *, kinds: Iterable[str] | None = None) -> bool:
    """Claims and executes at most one job. Returns False when the queue had
    nothing claimable. Handler exceptions never escape."""
    with session_factory() as db:
        job = claim_next(db, worker_id, kinds=kinds)
        if job is None:
            return False
        handler = HANDLERS.get(job.kind)
        if handler is None:
            fail(db, job, worker_id, f"no handler registered for {job.kind!r}", permanent=True)
            db.commit()
            return True
        try:
            handler(db, job)
            if not complete(db, job, worker_id):
                # Our lease was lost mid-run and another worker owns the job.
                # Discard anything this handler staged but did not commit.
                db.rollback()
                logger.warning("lost lease before completing kind=%s job_id=%s", job.kind, job.id)
                return True
            db.commit()
        except RetryLater as exc:
            db.rollback()
            fail(db, job, worker_id, _safe_error(exc), retry_in=exc.delay_seconds)
            db.commit()
        except PermanentFailure as exc:
            db.rollback()
            fail(db, job, worker_id, _safe_error(exc), permanent=True)
            db.commit()
        except Exception as exc:  # noqa: BLE001 - any handler failure is a retryable failure
            db.rollback()
            logger.exception("job handler failed kind=%s job_id=%s", job.kind, job.id)
            fail(db, job, worker_id, _safe_error(exc))
            db.commit()
        return True
