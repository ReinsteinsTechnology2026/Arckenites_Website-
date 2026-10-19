from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import require_permission, require_role
from app.crud.audit import write_audit_event
from app.database import get_db
from app.models.audit_log import AuthEventType
from app.models.job_application import JobApplication, JobApplicationReaction, JobApplicationStatus, JobReactionKind
from app.models.user import User
from app.schemas.job_applications import AdminJobOut, JobCreate, JobOut, JobReactionInput, JobUpdate


student_router = APIRouter(prefix="/student/job-applications", tags=["student-job-applications"])
admin_router = APIRouter(prefix="/admin/job-applications", tags=["admin-job-applications"])


def _visible():
    return (JobApplication.status == JobApplicationStatus.published,
            JobApplication.is_enabled.is_(True))


def _job(db: Session, job_id: int, *, student: bool = False, lock: bool = False) -> JobApplication:
    query = select(JobApplication).where(JobApplication.id == job_id)
    if student:
        query = query.where(*_visible())
    if lock:
        query = query.with_for_update()
    job = db.scalars(query).first()
    if job is None:
        raise HTTPException(status_code=404, detail="Job application not found")
    return job


def _summaries(db: Session, jobs: list[JobApplication], student_id: int | None = None):
    if not jobs:
        return []
    ids = [job.id for job in jobs]
    counts = {job_id: {} for job_id in ids}
    for job_id, kind, count in db.execute(
        select(JobApplicationReaction.job_id, JobApplicationReaction.kind, func.count())
        .where(JobApplicationReaction.job_id.in_(ids))
        .group_by(JobApplicationReaction.job_id, JobApplicationReaction.kind)
    ):
        counts[job_id][kind.value] = count
    mine = {}
    if student_id is not None:
        mine = dict(db.execute(
            select(JobApplicationReaction.job_id, JobApplicationReaction.kind)
            .where(JobApplicationReaction.job_id.in_(ids), JobApplicationReaction.student_id == student_id)
        ).all())
    return [dict(
        id=job.id, title=job.title, company=job.company, description=job.description,
        eligibility=job.eligibility, application_url=job.application_url,
        deadline=job.deadline, published_at=job.published_at,
        reaction_counts=counts[job.id], my_reaction=mine.get(job.id),
        status=job.status, is_enabled=job.is_enabled,
        created_at=job.created_at, updated_at=job.updated_at,
    ) for job in jobs]


def _audit(db: Session, actor: User, event: AuthEventType, job: JobApplication, request: Request, detail: str):
    write_audit_event(
        db, event, request.client.host if request.client else None,
        request.headers.get("user-agent"), user=actor, detail=detail,
        module="Job Applications", target=f"job_application:{job.id}",
        status="success", commit=False,
    )


@student_router.get("", response_model=list[JobOut])
def list_student_jobs(
    offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db), student: User = Depends(require_role("student")),
):
    jobs = db.scalars(select(JobApplication).where(*_visible())
                      .order_by(JobApplication.published_at.desc(), JobApplication.id.desc())
                      .offset(offset).limit(limit)).all()
    return _summaries(db, jobs, student.id)


@student_router.get("/{job_id}", response_model=JobOut)
def get_student_job(job_id: int, db: Session = Depends(get_db), student: User = Depends(require_role("student"))):
    return _summaries(db, [_job(db, job_id, student=True)], student.id)[0]


@student_router.put("/{job_id}/reaction", response_model=JobOut)
def set_reaction(
    job_id: int, payload: JobReactionInput, db: Session = Depends(get_db),
    student: User = Depends(require_role("student")),
):
    # Serializing on the parent row prevents concurrent requests from one
    # student creating two rows. The unique constraint is the final guard.
    job = _job(db, job_id, student=True, lock=True)
    reaction = db.scalar(select(JobApplicationReaction).where(
        JobApplicationReaction.job_id == job_id,
        JobApplicationReaction.student_id == student.id,
    ))
    if reaction is None:
        db.add(JobApplicationReaction(job_id=job_id, student_id=student.id, kind=payload.kind))
    else:
        reaction.kind = payload.kind
    db.commit()
    return _summaries(db, [job], student.id)[0]


@student_router.delete("/{job_id}/reaction", response_model=JobOut)
def remove_reaction(job_id: int, db: Session = Depends(get_db), student: User = Depends(require_role("student"))):
    job = _job(db, job_id, student=True, lock=True)
    reaction = db.scalar(select(JobApplicationReaction).where(
        JobApplicationReaction.job_id == job_id,
        JobApplicationReaction.student_id == student.id,
    ))
    if reaction is not None:
        db.delete(reaction)
        db.commit()
    return _summaries(db, [job], student.id)[0]


@admin_router.get("", response_model=list[AdminJobOut])
def list_admin_jobs(
    offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db), _actor: User = Depends(require_permission("placement.view")),
):
    jobs = db.scalars(select(JobApplication).order_by(JobApplication.created_at.desc(), JobApplication.id.desc())
                      .offset(offset).limit(limit)).all()
    return _summaries(db, jobs)


@admin_router.get("/{job_id}", response_model=AdminJobOut)
def get_admin_job(job_id: int, db: Session = Depends(get_db), _actor: User = Depends(require_permission("placement.view"))):
    return _summaries(db, [_job(db, job_id)])[0]


@admin_router.post("", response_model=AdminJobOut, status_code=status.HTTP_201_CREATED)
def create_job(payload: JobCreate, request: Request, db: Session = Depends(get_db),
               actor: User = Depends(require_permission("placement.create"))):
    job = JobApplication(**payload.model_dump(), created_by_id=actor.id,
                         status=JobApplicationStatus.draft, is_enabled=False)
    db.add(job)
    db.flush()
    _audit(db, actor, AuthEventType.job_application_created, job, request, "Created draft")
    db.commit()
    db.refresh(job)
    return _summaries(db, [job])[0]


@admin_router.patch("/{job_id}", response_model=AdminJobOut)
def update_job(job_id: int, payload: JobUpdate, request: Request, db: Session = Depends(get_db),
               actor: User = Depends(require_permission("placement.edit"))):
    job = _job(db, job_id, lock=True)
    if job.status == JobApplicationStatus.archived:
        raise HTTPException(status_code=409, detail="Archived announcements cannot be edited")
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(job, field, value)
    if changes:
        _audit(db, actor, AuthEventType.job_application_updated, job, request,
               "Updated fields: " + ", ".join(sorted(changes)))
        db.commit()
        db.refresh(job)
    return _summaries(db, [job])[0]


class StatusAction(BaseModel):
    model_config = {"extra": "forbid"}
    action: Literal["publish", "unpublish", "enable", "disable", "archive"]


@admin_router.post("/{job_id}/status", response_model=AdminJobOut)
def change_job_status(job_id: int, payload: StatusAction, request: Request,
                      db: Session = Depends(get_db), actor: User = Depends(require_permission("placement.edit"))):
    job = _job(db, job_id, lock=True)
    if job.status == JobApplicationStatus.archived:
        raise HTTPException(status_code=409, detail="Archived announcements cannot be changed")
    old = (job.status, job.is_enabled)
    if payload.action == "publish":
        job.status = JobApplicationStatus.published
        job.is_enabled = True
        if job.published_at is None:
            job.published_at = datetime.now(timezone.utc)
    elif payload.action == "unpublish":
        job.status = JobApplicationStatus.draft
        job.is_enabled = False
    elif payload.action == "enable":
        if job.status != JobApplicationStatus.published:
            raise HTTPException(status_code=409, detail="Publish this announcement before enabling it")
        job.is_enabled = True
    elif payload.action == "disable":
        job.is_enabled = False
    else:
        job.status = JobApplicationStatus.archived
        job.is_enabled = False
    if old != (job.status, job.is_enabled):
        _audit(db, actor, AuthEventType.job_application_status_changed, job, request, payload.action)
        db.commit()
        db.refresh(job)
    return _summaries(db, [job])[0]
