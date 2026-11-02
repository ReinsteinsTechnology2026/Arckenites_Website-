from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import settings
from app.core import class_join, class_lifecycle, class_runs
from app.core.rate_limit import limiter
from app.core.deps import get_current_user
from app.database import get_db
from app.models.batch import Batch
from app.models.class_run import REC_RECORDING, ClassSessionRun
from app.models.user import RoleEnum, User
from app.schemas.class_runs import ExtendRequest, JoinOut, PromptOut, RecordingApprovalRequest, RunRecordingStatusOut

router = APIRouter(prefix="/class-runs", tags=["class-runs"])


def _client_meta(request: Request) -> tuple[str, str]:
    ip = request.client.host if request.client else "unknown"
    return ip, request.headers.get("user-agent", "unknown")


def _load(db: Session, run_id: int) -> tuple[ClassSessionRun, Batch]:
    run = db.get(ClassSessionRun, run_id)
    batch = db.get(Batch, run.batch_id) if run is not None else None
    if run is None or batch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Class not found")
    return run, batch


def _require_visible(db: Session, user: User, batch: Batch) -> None:
    # 404 rather than 403 for outsiders: the existence of another batch's
    # class is not something to confirm to them.
    if not class_runs.can_view_status(db, user, batch):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Class not found")


def _require_controller(db: Session, user: User, batch: Batch) -> str:
    _require_visible(db, user, batch)
    controller = class_runs.recording_controller(db, user, batch)
    if controller is None:
        # Visible to the user but not theirs to control: this is the student
        # (or read-only admin) case, so an explicit refusal.
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Only the class trainer can start or approve recording.")
    return controller


def _status_out(db: Session, user: User, run: ClassSessionRun, batch: Batch) -> RunRecordingStatusOut:
    return RunRecordingStatusOut(
        run_id=run.id, batch_id=run.batch_id, run_state=run.state, recording_state=run.recording_state,
        approval_status=run.approval_status, approval_source=run.approval_source,
        approval_decided_at=run.approval_decided_at,
        can_control=class_runs.recording_controller(db, user, batch) is not None,
        is_recording=run.recording_state == REC_RECORDING,
        consent_version=settings.recording_consent_version, consent_text=class_runs.CONSENT_TEXT,
    )


@router.post("/batch/{batch_id}/join", response_model=JoinOut)
@limiter.limit("30/minute")
def join_batch_class(batch_id: int, request: Request, db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)):
    """Joins the batch's current class: the open scheduled occurrence, or - for
    the trainer only - starts an ad-hoc one that goes through the same approval
    workflow. Students can only join a class whose trainer is live."""
    batch = db.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Batch not found")
    run, role = class_join.resolve_run_for_batch(db, user, batch)
    joined = class_join.issue_join(db, user, run, role)
    return JoinOut(**joined, recording=_status_out(db, user, run, batch))


@router.post("/{run_id}/join", response_model=JoinOut)
@limiter.limit("30/minute")
def join_class(run_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    run, batch = _load(db, run_id)
    try:
        role = class_join.authorize_run_join(db, user, run, batch)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_403_FORBIDDEN:  # don't confirm another batch's class exists
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Class not found")
        raise
    joined = class_join.issue_join(db, user, run, role)
    return JoinOut(**joined, recording=_status_out(db, user, run, batch))


@router.get("/{run_id}/recording", response_model=RunRecordingStatusOut)
def get_recording_status(run_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    run, batch = _load(db, run_id)
    _require_visible(db, user, batch)
    return _status_out(db, user, run, batch)


@router.post("/{run_id}/recording/prompt", response_model=PromptOut)
def claim_recording_prompt(
    run_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    """Called once when the trainer enters the room. Returns show=true exactly
    once per occurrence; afterwards the room uses the persistent control."""
    run, batch = _load(db, run_id)
    _require_controller(db, user, batch)
    ip, ua = _client_meta(request)
    show = class_runs.claim_prompt(db, user, run, ip, ua)
    return PromptOut(show=show, consent_version=settings.recording_consent_version,
                     consent_text=class_runs.CONSENT_TEXT)


@router.post("/{run_id}/recording/approval", response_model=RunRecordingStatusOut)
def decide_recording_approval(
    run_id: int, payload: RecordingApprovalRequest, request: Request,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    run, batch = _load(db, run_id)
    _require_controller(db, user, batch)
    ip, ua = _client_meta(request)
    run, _changed = class_runs.decide_approval(
        db, user, run_id, decision=payload.decision, consent_version=payload.consent_version, ip=ip, ua=ua,
    )
    return _status_out(db, user, run, batch)


def _end(run_id: int, reason: str, db: Session, user: User) -> RunRecordingStatusOut:
    run, batch = _load(db, run_id)
    _require_controller(db, user, batch)
    if run.state == "SCHEDULED":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This class hasn't opened yet.")
    class_lifecycle.trainer_end(db, run_id, user, reason)
    db.commit()
    db.refresh(run)
    return _status_out(db, user, run, batch)


@router.post("/{run_id}/end", response_model=RunRecordingStatusOut)
def end_class(run_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """The trainer ends the class: it closes for everyone and any recording
    starts finalizing immediately. Idempotent."""
    return _end(run_id, class_lifecycle.END_TRAINER_ENDED, db, user)


@router.post("/{run_id}/trainer-leave", response_model=RunRecordingStatusOut)
def trainer_leaves_for_good(run_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """The trainer deliberately leaves (not a dropped connection): the class
    ends for everyone and any recording is finalized. A dropped connection is
    different - it only starts the reconnect grace period."""
    return _end(run_id, class_lifecycle.END_TRAINER_LEFT, db, user)


@router.post("/{run_id}/extend", response_model=RunRecordingStatusOut)
def extend_class(run_id: int, payload: ExtendRequest, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    run, batch = _load(db, run_id)
    _require_controller(db, user, batch)
    class_lifecycle.extend_run(db, run_id, user, payload.minutes)
    db.commit()
    db.refresh(run)
    return _status_out(db, user, run, batch)
