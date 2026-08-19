from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.deps import require_role
from app.database import get_db
from app.models.batch import Batch, BatchEnrollment, BatchStatusEnum, BatchTypeEnum
from app.models.program import Program
from app.models.user import RoleEnum, User
from app.schemas.admin_batches import (
    AllocateStudentsRequest,
    BatchDetailOut,
    BatchListItemOut,
    BatchStatsOut,
    CreateBatchRequest,
    ProgramSummary,
    StudentSummary,
    TrainerSummary,
    UpdateBatchRequest,
)

router = APIRouter(prefix="/admin/batches", tags=["admin-batches"])


def _get_batch_or_404(db: Session, batch_id: int) -> Batch:
    batch = db.get(
        Batch, batch_id,
        options=[selectinload(Batch.enrollments).selectinload(BatchEnrollment.student), selectinload(Batch.program)],
    )
    if batch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Batch not found")
    return batch


def _validate_program(db: Session, program_id: int) -> Program:
    program = db.get(Program, program_id)
    if program is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="program_id must reference an existing program.")
    return program


def _class_days_list(value: str | None) -> list[str]:
    return [d for d in value.split(",") if d] if value else []


def _trainer_summary(batch: Batch) -> TrainerSummary | None:
    return TrainerSummary(id=batch.trainer.id, full_name=batch.trainer.full_name, username=batch.trainer.username) if batch.trainer else None


def _program_summary(batch: Batch) -> ProgramSummary | None:
    return ProgramSummary(id=batch.program.id, name=batch.program.name, slug=batch.program.slug) if batch.program else None


def _list_item(batch: Batch) -> BatchListItemOut:
    return BatchListItemOut(
        id=batch.id, name=batch.name, course=batch.course,
        batch_type=batch.batch_type.value, status=batch.status.value,
        trainer=_trainer_summary(batch), program=_program_summary(batch), student_count=len(batch.enrollments),
        max_capacity=batch.max_capacity, start_date=batch.start_date, end_date=batch.end_date,
        class_days=_class_days_list(batch.class_days), start_time=batch.start_time, end_time=batch.end_time,
    )


def _detail(batch: Batch) -> BatchDetailOut:
    base = _list_item(batch).model_dump()
    students = [
        StudentSummary(id=e.student.id, full_name=e.student.full_name, username=e.student.username, joined_at=e.joined_at)
        for e in sorted(batch.enrollments, key=lambda e: e.joined_at or e.id)
    ]
    return BatchDetailOut(**base, students=students)


def _validate_trainer(db: Session, trainer_id: int) -> User:
    trainer = db.get(User, trainer_id)
    if trainer is None or trainer.role != RoleEnum.staff:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="trainer_id must reference an existing trainer.")
    return trainer


def _validate_students(db: Session, student_ids: list[int]) -> list[User]:
    if not student_ids:
        return []
    students = db.scalars(select(User).where(User.id.in_(student_ids), User.role == RoleEnum.student)).all()
    found_ids = {s.id for s in students}
    missing = set(student_ids) - found_ids
    if missing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown student id(s): {', '.join(map(str, sorted(missing)))}")
    return students


@router.get("/stats", response_model=BatchStatsOut)
def batch_stats(db: Session = Depends(get_db), _admin: User = Depends(require_role("admin"))):
    rows = db.execute(select(Batch.status, func.count()).group_by(Batch.status)).all()
    counts = {status_val.value: count for status_val, count in rows}
    total = sum(counts.values())
    return BatchStatsOut(
        total=total,
        active=counts.get(BatchStatusEnum.active.value, 0),
        upcoming=counts.get(BatchStatusEnum.upcoming.value, 0),
        completed=counts.get(BatchStatusEnum.completed.value, 0),
    )


@router.get("", response_model=list[BatchListItemOut])
def list_batches(
    program_id: int | None = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_role("admin")),
):
    query = select(Batch).options(
        selectinload(Batch.trainer), selectinload(Batch.program), selectinload(Batch.enrollments)
    )
    if program_id is not None:
        query = query.where(Batch.program_id == program_id)
    batches = db.scalars(query.order_by(Batch.created_at.desc())).all()
    return [_list_item(b) for b in batches]


@router.post("", response_model=BatchDetailOut, status_code=201)
def create_batch(
    payload: CreateBatchRequest,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_role("admin")),
):
    if payload.trainer_id is not None:
        _validate_trainer(db, payload.trainer_id)
    if payload.program_id is not None:
        _validate_program(db, payload.program_id)
    students = _validate_students(db, payload.student_ids)

    batch = Batch(
        name=payload.name, course=payload.course,
        batch_type=BatchTypeEnum(payload.batch_type), status=BatchStatusEnum(payload.status),
        trainer_id=payload.trainer_id, program_id=payload.program_id,
        start_date=payload.start_date, end_date=payload.end_date,
        max_capacity=payload.max_capacity,
        class_days=",".join(payload.class_days) if payload.class_days else None,
        start_time=payload.start_time, end_time=payload.end_time,
    )
    db.add(batch)
    db.flush()

    for student in students:
        db.add(BatchEnrollment(batch_id=batch.id, student_id=student.id))

    db.commit()
    return _detail(_get_batch_or_404(db, batch.id))


@router.get("/{batch_id}", response_model=BatchDetailOut)
def get_batch(batch_id: int, db: Session = Depends(get_db), _admin: User = Depends(require_role("admin"))):
    return _detail(_get_batch_or_404(db, batch_id))


@router.patch("/{batch_id}", response_model=BatchDetailOut)
def update_batch(
    batch_id: int,
    payload: UpdateBatchRequest,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_role("admin")),
):
    batch = _get_batch_or_404(db, batch_id)

    if payload.name is not None:
        batch.name = payload.name
    if payload.course is not None:
        batch.course = payload.course
    if payload.batch_type is not None:
        batch.batch_type = BatchTypeEnum(payload.batch_type)
    if payload.status is not None:
        batch.status = BatchStatusEnum(payload.status)
    if payload.clear_trainer:
        batch.trainer_id = None
    elif payload.trainer_id is not None:
        _validate_trainer(db, payload.trainer_id)
        batch.trainer_id = payload.trainer_id
    if payload.clear_program:
        batch.program_id = None
    elif payload.program_id is not None:
        _validate_program(db, payload.program_id)
        batch.program_id = payload.program_id
    if payload.start_date is not None:
        batch.start_date = payload.start_date
    if payload.end_date is not None:
        batch.end_date = payload.end_date
    if payload.max_capacity is not None:
        batch.max_capacity = payload.max_capacity
    if payload.class_days is not None:
        batch.class_days = ",".join(payload.class_days) if payload.class_days else None
    if payload.start_time is not None:
        batch.start_time = payload.start_time
    if payload.end_time is not None:
        batch.end_time = payload.end_time

    db.add(batch)
    db.commit()
    return _detail(_get_batch_or_404(db, batch_id))


@router.delete("/{batch_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_batch(batch_id: int, db: Session = Depends(get_db), _admin: User = Depends(require_role("admin"))):
    batch = _get_batch_or_404(db, batch_id)
    db.delete(batch)  # cascades to batch_enrollments via the Batch.enrollments relationship
    db.commit()
    return None


@router.post("/{batch_id}/students", response_model=BatchDetailOut)
def allocate_students(
    batch_id: int,
    payload: AllocateStudentsRequest,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_role("admin")),
):
    batch = _get_batch_or_404(db, batch_id)
    students = _validate_students(db, payload.student_ids)

    existing_ids = {e.student_id for e in batch.enrollments}
    if batch.max_capacity is not None:
        new_ids = {s.id for s in students} - existing_ids
        if len(existing_ids) + len(new_ids) > batch.max_capacity:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Adding these students would exceed the batch's capacity of {batch.max_capacity}.",
            )

    for student in students:
        if student.id not in existing_ids:
            db.add(BatchEnrollment(batch_id=batch.id, student_id=student.id))

    db.commit()
    return _detail(_get_batch_or_404(db, batch_id))


@router.delete("/{batch_id}/students/{student_id}", response_model=BatchDetailOut)
def remove_student(
    batch_id: int,
    student_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_role("admin")),
):
    batch = _get_batch_or_404(db, batch_id)
    enrollment = next((e for e in batch.enrollments if e.student_id == student_id), None)
    if enrollment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student is not in this batch.")

    db.delete(enrollment)
    db.commit()
    return _detail(_get_batch_or_404(db, batch_id))


@router.get("/lookup/available-students", response_model=list[dict])
def available_students(
    q: str = "",
    db: Session = Depends(get_db),
    _admin: User = Depends(require_role("admin")),
):
    """Backs the student search box on the allocate-students panel."""
    query = select(User).where(User.role == RoleEnum.student, User.is_active.is_(True))
    if q:
        like = f"%{q}%"
        query = query.where(or_(User.username.ilike(like), User.full_name.ilike(like)))
    students = db.scalars(query.order_by(User.full_name).limit(50)).all()
    return [{"id": s.id, "full_name": s.full_name, "username": s.username} for s in students]
