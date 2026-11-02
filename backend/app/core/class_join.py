"""Joining a class: pick the right occurrence, re-check eligibility, issue a token.

Every call re-checks the person's CURRENT standing (trainer of this batch, or
currently enrolled), so a student removed from a batch, or a trainer reassigned
away, cannot get a fresh token. Tokens are short-lived and room-scoped; a
participant removed mid-class cannot rejoin because the next token request is
refused.
"""
from datetime import datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.core import class_lifecycle as lifecycle
from app.core import class_runs
from app.core.class_runs import utcnow
from app.core.livekit_media import room_join_token
from app.core.media_events import identity_for_user
from app.core.media_server import MediaServerUnavailable, get_media_server, room_name_for_run
from app.models.batch import Batch
from app.models.class_run import (
    RUN_KIND_SCHEDULED,
    RUN_LIVE,
    RUN_OPEN_STATES,
    RUN_ROOM_OPEN,
    RUN_SCHEDULED,
    ClassSessionRun,
)
from app.models.user import RoleEnum, User

# A trainer arriving this long before a scheduled class is taken to be starting
# THAT class, not an unrelated ad-hoc one.
EARLY_ARRIVAL_WINDOW = timedelta(minutes=60)


def _forbidden() -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You don't have access to this batch.")


def role_in_batch(db: Session, user: User, batch: Batch) -> str:
    """'trainer' or 'student'; raises 403 for anyone else (including admins,
    who have no seat in a class)."""
    if user.role == RoleEnum.staff and batch.trainer_id == user.id:
        return "trainer"
    if user.role == RoleEnum.student and class_runs.is_enrolled(db, user, batch):
        return "student"
    raise _forbidden()


def _require_runnable_batch(batch: Batch) -> None:
    from app.core.class_lifecycle import ELIGIBLE_BATCH_STATUSES
    if batch.status not in ELIGIBLE_BATCH_STATUSES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This batch isn't running classes right now.")


def _open_run(db: Session, batch_id: int, *, live_only: bool) -> ClassSessionRun | None:
    states = (RUN_LIVE,) if live_only else RUN_OPEN_STATES
    runs = db.scalars(select(ClassSessionRun).where(
        ClassSessionRun.batch_id == batch_id, ClassSessionRun.state.in_(states)
    ).order_by(ClassSessionRun.actual_start_at.desc().nulls_last(), ClassSessionRun.id.desc())).all()
    # prefer a class that is actually live over one merely open
    return next((r for r in runs if r.state == RUN_LIVE), runs[0] if runs else None)


def resolve_run_for_batch(db: Session, user: User, batch: Batch, now: datetime | None = None) -> tuple[ClassSessionRun, str]:
    """Finds (or, for the trainer, opens) the occurrence this person should be
    joining. Commits any occurrence it creates or activates."""
    now = now or utcnow()
    role = role_in_batch(db, user, batch)
    _require_runnable_batch(batch)

    if role == "student":
        run = _open_run(db, batch.id, live_only=True)
        if run is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail="Your trainer hasn't started this class yet. Please wait.")
        return run, role

    run = _open_run(db, batch.id, live_only=False)
    if run is None:
        due = db.scalars(select(ClassSessionRun).where(
            ClassSessionRun.batch_id == batch.id, ClassSessionRun.kind == RUN_KIND_SCHEDULED,
            ClassSessionRun.state == RUN_SCHEDULED,
            ClassSessionRun.scheduled_start_at <= now + EARLY_ARRIVAL_WINDOW,
            ClassSessionRun.effective_end_at > now,
        ).order_by(ClassSessionRun.scheduled_start_at)).first()
        if due is not None:
            lifecycle.activate_run(db, due.id, now)
            db.commit()
            db.refresh(due)
            run = due if due.state in RUN_OPEN_STATES else None
    if run is None:
        run = lifecycle.open_adhoc_run(db, batch, now)  # ad hoc: same occurrence + approval workflow
        db.commit()
    return run, role


def authorize_run_join(db: Session, user: User, run: ClassSessionRun, batch: Batch) -> str:
    role = role_in_batch(db, user, batch)
    _require_runnable_batch(batch)
    if run.state not in RUN_OPEN_STATES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This class isn't open.")
    if role == "student" and run.state != RUN_LIVE:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Your trainer hasn't started this class yet. Please wait.")
    return role


def issue_join(db: Session, user: User, run: ClassSessionRun, role: str) -> dict:
    """Ensures the room exists on the media server and mints this user's token."""
    if not settings.livekit_configured:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="The meeting server isn't available yet.")
    room = room_name_for_run(run.id)
    try:
        get_media_server().ensure_room(room, metadata=f'{{"run":{run.id}}}')
    except MediaServerUnavailable:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="The meeting server isn't reachable right now. Please try again shortly.")
    identity = identity_for_user(user.id)
    token = room_join_token(identity=identity, name=user.full_name, room=room, is_trainer=role == "trainer", run_id=run.id)
    return {
        "run_id": run.id, "batch_id": run.batch_id, "run_state": run.state, "livekit_url": settings.livekit_url,
        "token": token, "room_name": room, "identity": identity, "display_name": user.full_name,
        "is_trainer": role == "trainer", "expires_in": settings.livekit_token_ttl_seconds,
    }
