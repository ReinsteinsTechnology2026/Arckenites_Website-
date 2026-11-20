"""A student's private notes for a class occurrence.

The caller is always the owner: there is no user id in any path or body. Only a
currently enrolled student may read or write; the trainer, admins and outsiders
are refused, so nobody can reach another student's notes.
"""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import class_join
from app.core.deps import get_current_user
from app.core.rate_limit import limiter
from app.database import get_db
from app.models.batch import Batch
from app.models.class_note import ClassPrivateNote
from app.models.class_run import ClassSessionRun
from app.models.user import User
from app.schemas.class_notes import PrivateNoteIn, PrivateNoteOut

router = APIRouter(prefix="/class-runs", tags=["class-notes"])


def _own_student_run(db: Session, user: User, run_id: int) -> ClassSessionRun:
    run = db.get(ClassSessionRun, run_id)
    batch = db.get(Batch, run.batch_id) if run is not None else None
    if run is None or batch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Class not found")
    try:
        role = class_join.role_in_batch(db, user, batch)   # current standing; admins/outsiders raise 403
    except HTTPException:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Class not found")  # don't confirm it exists
    if role != "student":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Private notes are for students.")
    return run


@router.get("/{run_id}/notes/me", response_model=PrivateNoteOut)
def get_my_notes(run_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    run = _own_student_run(db, user, run_id)
    note = db.scalar(select(ClassPrivateNote).where(ClassPrivateNote.run_id == run.id, ClassPrivateNote.user_id == user.id))
    return PrivateNoteOut(run_id=run.id, content=note.content if note else "", updated_at=note.updated_at if note else None)


# POST (an idempotent upsert), not PUT: the class-runs routes deliberately expose no PUT/PATCH/DELETE.
@router.post("/{run_id}/notes/me", response_model=PrivateNoteOut)
@limiter.limit("120/minute")
def save_my_notes(run_id: int, payload: PrivateNoteIn, request: Request, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    run = _own_student_run(db, user, run_id)
    note = db.scalar(select(ClassPrivateNote).where(ClassPrivateNote.run_id == run.id, ClassPrivateNote.user_id == user.id))
    if note is None:
        note = ClassPrivateNote(run_id=run.id, user_id=user.id, content=payload.content)
        db.add(note)
    else:
        note.content = payload.content
    db.commit()
    db.refresh(note)
    return PrivateNoteOut(run_id=run.id, content=note.content, updated_at=note.updated_at)
