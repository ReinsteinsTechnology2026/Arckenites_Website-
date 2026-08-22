from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.deps import require_permission
from app.database import get_db
from app.models.interview_schedule import InterviewModeEnum, InterviewSchedule, InterviewStatusEnum
from app.models.user import RoleEnum, User
from app.schemas.interview_schedule import CreateInterviewRequest, InterviewOut, UpdateInterviewRequest

router = APIRouter(prefix="/admin/interviews", tags=["admin-interviews"])


def _out(interview: InterviewSchedule, student: User) -> InterviewOut:
    return InterviewOut(
        id=interview.id, student_id=interview.student_id,
        student_name=student.full_name, student_username=student.username,
        company_name=interview.company_name, role=interview.role,
        interview_date=interview.interview_date, interview_time=interview.interview_time,
        mode=interview.mode.value, location_or_link=interview.location_or_link,
        status=interview.status.value, notes=interview.notes, created_at=interview.created_at,
    )


@router.get("", response_model=list[InterviewOut])
def list_interviews(db: Session = Depends(get_db), _actor: User = Depends(require_permission("placement.view"))):
    rows = db.execute(
        select(InterviewSchedule, User)
        .join(User, User.id == InterviewSchedule.student_id)
        .order_by(InterviewSchedule.interview_date.desc())
    ).all()
    return [_out(i, s) for i, s in rows]


@router.post("", response_model=InterviewOut, status_code=201)
def create_interview(
    payload: CreateInterviewRequest,
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("placement.create")),
):
    student = db.get(User, payload.student_id)
    if student is None or student.role != RoleEnum.student:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="student_id must reference an existing student.")

    interview = InterviewSchedule(
        student_id=payload.student_id, company_name=payload.company_name, role=payload.role,
        interview_date=payload.interview_date, interview_time=payload.interview_time,
        mode=InterviewModeEnum(payload.mode), location_or_link=payload.location_or_link, notes=payload.notes,
    )
    db.add(interview)
    db.commit()
    db.refresh(interview)
    return _out(interview, student)


def _get_interview_or_404(db: Session, interview_id: int) -> InterviewSchedule:
    interview = db.get(InterviewSchedule, interview_id)
    if interview is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Interview not found")
    return interview


@router.patch("/{interview_id}", response_model=InterviewOut)
def update_interview(
    interview_id: int,
    payload: UpdateInterviewRequest,
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("placement.edit")),
):
    interview = _get_interview_or_404(db, interview_id)

    if payload.company_name is not None:
        interview.company_name = payload.company_name
    if payload.role is not None:
        interview.role = payload.role
    if payload.interview_date is not None:
        interview.interview_date = payload.interview_date
    if payload.interview_time is not None:
        interview.interview_time = payload.interview_time
    if payload.mode is not None:
        interview.mode = InterviewModeEnum(payload.mode)
    if payload.location_or_link is not None:
        interview.location_or_link = payload.location_or_link
    if payload.status is not None:
        interview.status = InterviewStatusEnum(payload.status)
    if payload.notes is not None:
        interview.notes = payload.notes

    db.add(interview)
    db.commit()
    db.refresh(interview)
    return _out(interview, interview.student)


@router.delete("/{interview_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_interview(
    interview_id: int,
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("placement.delete")),
):
    interview = _get_interview_or_404(db, interview_id)
    db.delete(interview)
    db.commit()
    return None


@router.get("/lookup/students", response_model=list[dict])
def available_students(
    q: str = "",
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("placement.view")),
):
    """Backs the student search box on the schedule-interview panel."""
    query = select(User).where(User.role == RoleEnum.student, User.is_active.is_(True))
    if q:
        like = f"%{q}%"
        query = query.where(or_(User.username.ilike(like), User.full_name.ilike(like)))
    students = db.scalars(query.order_by(User.full_name).limit(50)).all()
    return [{"id": s.id, "full_name": s.full_name, "username": s.username} for s in students]
