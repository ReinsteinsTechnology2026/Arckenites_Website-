"""In-class actions that need the media server: raise hand and trainer moderation.

Students cannot publish data messages and have no room-admin right, so these go
through the backend, which authorizes first and then acts on exactly ONE room
(the class's own room) through the media-server interface:

  * raise/lower hand - an enrolled student changes only their OWN participant
    attribute; the trainer's client is notified by LiveKit;
  * mute / remove / lower a hand - only the batch's assigned trainer, and only
    against a currently enrolled student of that batch (never the trainer, an
    admin or an arbitrary identity).

Nothing here touches recording.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import class_join
from app.core.deps import get_current_user
from app.core.media_events import identity_for_user, user_id_from_identity
from app.core.media_server import MediaServerUnavailable, get_media_server, room_name_for_run
from app.core.rate_limit import limiter
from app.database import get_db
from app.models.batch import Batch, BatchEnrollment
from app.models.class_run import RUN_OPEN_STATES, ClassSessionRun
from app.models.user import User

logger = logging.getLogger("class_actions")
router = APIRouter(prefix="/class-runs", tags=["class-actions"])


class HandRequest(BaseModel):
    raised: bool


class TargetRequest(BaseModel):
    identity: str = Field(min_length=2, max_length=64)


def _live_run(db: Session, run_id: int) -> tuple[ClassSessionRun, Batch]:
    run = db.get(ClassSessionRun, run_id)
    batch = db.get(Batch, run.batch_id) if run is not None else None
    if run is None or batch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Class not found")
    return run, batch


def _role(db: Session, user: User, batch: Batch) -> str:
    try:
        return class_join.role_in_batch(db, user, batch)
    except HTTPException:  # admins and outsiders: don't confirm the class exists
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Class not found")


def _require_open(run: ClassSessionRun) -> None:
    if run.state not in RUN_OPEN_STATES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This class isn't running.")


def _media_call(fn):
    try:
        fn(get_media_server())
    except MediaServerUnavailable:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="The classroom is temporarily unavailable. Please try again.")


@router.post("/{run_id}/hand", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("60/minute")
def set_my_hand(run_id: int, payload: HandRequest, request: Request, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    run, batch = _live_run(db, run_id)
    if _role(db, user, batch) != "student":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only students raise their hand.")
    _require_open(run)
    room, identity = room_name_for_run(run.id), identity_for_user(user.id)   # always the caller's own identity
    _media_call(lambda media: media.set_participant_attributes(room, identity, {"hand": "1" if payload.raised else ""}))


def _trainer_target(db: Session, user: User, run_id: int, identity: str) -> tuple[ClassSessionRun, str, str]:
    run, batch = _live_run(db, run_id)
    if _role(db, user, batch) != "trainer":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the class trainer can do that.")
    _require_open(run)
    target_id = user_id_from_identity(identity)
    enrolled = target_id is not None and db.scalar(select(BatchEnrollment.id).where(
        BatchEnrollment.batch_id == batch.id, BatchEnrollment.student_id == target_id)) is not None
    if not enrolled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found in this class.")
    return run, room_name_for_run(run.id), identity_for_user(target_id)


@router.post("/{run_id}/moderation/mute", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("60/minute")
def mute_student(run_id: int, payload: TargetRequest, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    run, room, identity = _trainer_target(db, user, run_id, payload.identity)
    _media_call(lambda media: media.mute_participant_microphone(room, identity))
    logger.info("moderation mute run_id=%s actor=%s target=%s", run.id, user.id, identity)


@router.post("/{run_id}/moderation/kick", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("60/minute")
def kick_student(run_id: int, payload: TargetRequest, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    run, room, identity = _trainer_target(db, user, run_id, payload.identity)
    _media_call(lambda media: media.remove_participant(room, identity))
    logger.info("moderation kick run_id=%s actor=%s target=%s", run.id, user.id, identity)


@router.post("/{run_id}/moderation/lower-hand", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("60/minute")
def lower_student_hand(run_id: int, payload: TargetRequest, request: Request, db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    run, room, identity = _trainer_target(db, user, run_id, payload.identity)
    _media_call(lambda media: media.set_participant_attributes(room, identity, {"hand": ""}))
