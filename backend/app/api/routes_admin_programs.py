from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.deps import require_permission
from app.core.approved_programs import approved_name, require_approved_program, resolve_approved_catalog
from app.crud.audit import write_audit_event
from app.database import get_db
from app.models.audit_log import AuthEventType
from app.models.batch import Batch, BatchEnrollment, BatchStatusEnum
from app.models.program import EnrollmentStatusEnum, Program, ProgramEnrollment, ProgramModeEnum, ProgramStatusEnum
from app.models.user import RoleEnum, User
from app.schemas.admin_programs import (
    BatchStatCounts,
    CreateProgramRequest,
    CurrentBatchSummary,
    EnrollmentOut,
    EnrollmentStatCounts,
    EnrollStudentsRequest,
    ProgramBatchSummary,
    ProgramDetailOut,
    ProgramListItemOut,
    ProgramStatsOut,
    UpdateEnrollmentRequest,
    UpdateProgramRequest,
)

router = APIRouter(prefix="/admin/programs", tags=["admin-programs"])

# Enrollment states that count as "actually enrolled" for headline counts —
# pending hasn't been approved yet, rejected/withdrawn are no longer in it.
_ENROLLED_STATES = (
    EnrollmentStatusEnum.approved, EnrollmentStatusEnum.allocated,
    EnrollmentStatusEnum.active, EnrollmentStatusEnum.completed,
)


def _client_meta(request: Request) -> tuple[str, str]:
    ip = request.client.host if request.client else "unknown"
    ua = request.headers.get("user-agent", "unknown")
    return ip, ua


def _get_approved_program_or_404(db: Session, program_id: int) -> Program:
    """Admin catalog views and edits cover only the three approved programs.

    Legacy rows stay in the database for history, but are not exposed here.
    """
    program = next((p for p in resolve_approved_catalog(db).programs if p.id == program_id), None)
    if program is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Program not found in the approved catalog")
    return program


def _lock_catalog(db: Session) -> None:
    # Serialize API catalog writes, including normalized-name checks.
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(19041702)"))


def _active_batch_count(db: Session, program_id: int) -> int:
    return db.scalar(
        select(func.count()).select_from(Batch)
        .where(Batch.program_id == program_id, Batch.status == BatchStatusEnum.active)
    ) or 0


def _enrolled_count(db: Session, program_id: int) -> int:
    return db.scalar(
        select(func.count()).select_from(ProgramEnrollment)
        .where(ProgramEnrollment.program_id == program_id, ProgramEnrollment.status.in_(_ENROLLED_STATES))
    ) or 0


def _list_item(db: Session, program: Program) -> ProgramListItemOut:
    return ProgramListItemOut(
        id=program.id, slug=program.slug, name=approved_name(program.name) or program.name, code=program.code,
        description=program.description, category=program.category,
        mode=program.mode.value, status=program.status.value,
        enrolled_count=_enrolled_count(db, program.id),
        active_batch_count=_active_batch_count(db, program.id),
        created_at=program.created_at,
    )


@router.get("/stats", response_model=ProgramStatsOut)
def program_stats(db: Session = Depends(get_db), _actor: User = Depends(require_permission("programs.view"))):
    catalog = resolve_approved_catalog(db)
    programs = catalog.programs
    approved_ids = [p.id for p in programs]
    total_programs = len(programs)
    active_programs = sum(p.status == ProgramStatusEnum.active for p in programs)
    total_enrolled_students = db.scalar(
        select(func.count(func.distinct(ProgramEnrollment.student_id)))
        .where(ProgramEnrollment.status.in_(_ENROLLED_STATES), ProgramEnrollment.program_id.in_(approved_ids))
    ) or 0
    programs_with_upcoming_batches = db.scalar(
        select(func.count(func.distinct(Batch.program_id)))
        .where(Batch.status == BatchStatusEnum.upcoming, Batch.program_id.in_(approved_ids))
    ) or 0

    return ProgramStatsOut(
        total_programs=total_programs, active_programs=active_programs,
        inactive_programs=total_programs - active_programs,
        total_enrolled_students=total_enrolled_students,
        programs_with_upcoming_batches=programs_with_upcoming_batches,
        catalog_notice=catalog.notice,
    )


@router.get("", response_model=list[ProgramListItemOut])
def list_programs(db: Session = Depends(get_db), _actor: User = Depends(require_permission("programs.view"))):
    return [_list_item(db, p) for p in resolve_approved_catalog(db).programs]


@router.post("", response_model=ProgramListItemOut, status_code=201)
def create_program(
    payload: CreateProgramRequest,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("programs.create")),
):
    # The catalog is closed to the three approved programs. Missing approved
    # records are added only by a reviewed migration, never through this API.
    raise HTTPException(
        status.HTTP_403_FORBIDDEN,
        "The program catalog is limited to the approved programs. New programs cannot be created here.",
    )


@router.get("/{program_id}", response_model=ProgramDetailOut)
def get_program(program_id: int, db: Session = Depends(get_db), _actor: User = Depends(require_permission("programs.view"))):
    program = _get_approved_program_or_404(db, program_id)
    is_approved = True

    batches = db.scalars(
        select(Batch)
        .where(Batch.program_id == program_id)
        .options(selectinload(Batch.trainer), selectinload(Batch.enrollments))
        .order_by(Batch.created_at.desc())
    ).all()
    batch_stats = BatchStatCounts(
        active=sum(1 for b in batches if b.status == BatchStatusEnum.active),
        upcoming=sum(1 for b in batches if b.status == BatchStatusEnum.upcoming),
        completed=sum(1 for b in batches if b.status == BatchStatusEnum.completed),
    )
    batch_summaries = [
        ProgramBatchSummary(
            id=b.id, name=b.name, status=b.status.value,
            trainer_name=b.trainer.full_name if b.trainer else None,
            student_count=len(b.enrollments),
        ) for b in batches
    ]

    # Which batch (if any, under this program) each enrolled student currently sits in —
    # backs the "transfer between batches" UI without duplicating student/trainer data.
    batch_ids = [b.id for b in batches]
    current_batch_by_student: dict[int, CurrentBatchSummary] = {}
    if batch_ids:
        rows = db.execute(
            select(BatchEnrollment.student_id, Batch.id, Batch.name)
            .join(Batch, Batch.id == BatchEnrollment.batch_id)
            .where(BatchEnrollment.batch_id.in_(batch_ids))
        ).all()
        for student_id, batch_id, batch_name in rows:
            current_batch_by_student[student_id] = CurrentBatchSummary(id=batch_id, name=batch_name)

    enrollment_rows = db.execute(
        select(ProgramEnrollment, User.full_name, User.username)
        .join(User, User.id == ProgramEnrollment.student_id)
        .where(ProgramEnrollment.program_id == program_id)
        .order_by(ProgramEnrollment.enrolled_at.desc())
    ).all()

    counts = {s.value: 0 for s in EnrollmentStatusEnum}
    enrollments: list[EnrollmentOut] = []
    for pe, full_name, username in enrollment_rows:
        counts[pe.status.value] += 1
        enrollments.append(EnrollmentOut(
            id=pe.id, student_id=pe.student_id, student_name=full_name, student_username=username,
            status=pe.status.value, enrolled_at=pe.enrolled_at,
            current_batch=current_batch_by_student.get(pe.student_id),
        ))

    enrollment_stats = EnrollmentStatCounts(total=len(enrollment_rows), **{k: v for k, v in counts.items()})

    return ProgramDetailOut(
        id=program.id, slug=program.slug, name=approved_name(program.name) or program.name, code=program.code,
        description=program.description, category=program.category, duration=program.duration,
        eligibility=program.eligibility, objectives=program.objectives, max_capacity=program.max_capacity,
        mode=program.mode.value, status=program.status.value, created_at=program.created_at,
        enrollment_stats=enrollment_stats, batch_stats=batch_stats,
        batches=batch_summaries, enrollments=enrollments,
        is_approved=is_approved,
        allows_new_enrollments=is_approved and program.status == ProgramStatusEnum.active,
    )


@router.patch("/{program_id}", response_model=ProgramDetailOut)
def update_program(
    program_id: int,
    payload: UpdateProgramRequest,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("programs.edit")),
):
    _lock_catalog(db)
    program = _get_approved_program_or_404(db, program_id)
    if payload.name is not None:
        name = " ".join(payload.name.split())
        if not name:
            raise HTTPException(400, "Program name is required.")
        if approved_name(name) != approved_name(program.name):
            raise HTTPException(400, "Approved program identities cannot be renamed.")

    if payload.code is not None and payload.code != program.code:
        existing = db.scalar(select(Program).where(Program.code == payload.code, Program.id != program_id))
        if existing is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="That program code is already in use.")
        program.code = payload.code

    # Name is an identity field for this closed catalog. Accept unchanged
    # labels from existing clients without rewriting the persisted name.
    if payload.description is not None:
        program.description = payload.description
    if payload.category is not None:
        program.category = payload.category
    if payload.duration is not None:
        program.duration = payload.duration
    if payload.eligibility is not None:
        program.eligibility = payload.eligibility
    if payload.objectives is not None:
        program.objectives = payload.objectives
    if payload.max_capacity is not None:
        program.max_capacity = payload.max_capacity
    if payload.mode is not None:
        program.mode = ProgramModeEnum(payload.mode)
    if payload.status is not None:
        program.status = ProgramStatusEnum(payload.status)

    try:
        db.add(program)
        ip, ua = _client_meta(request)
        write_audit_event(
            db, AuthEventType.program_updated, ip, ua, user=actor, detail=f"Updated program id={program.id}",
            module="programs", target=f"program:{program.id}", status="success", commit=False,
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Program name or code conflicts with an existing record.")
    except Exception:
        db.rollback()
        raise HTTPException(500, "Program update failed. No changes were made.")

    return get_program(program_id, db, actor)


@router.post("/{program_id}/enrollments", response_model=ProgramDetailOut)
def enroll_students(
    program_id: int,
    payload: EnrollStudentsRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("programs.edit")),
):
    program = require_approved_program(db, program_id)

    students = db.scalars(
        select(User).where(User.id.in_(payload.student_ids), User.role == RoleEnum.student)
    ).all()
    found_ids = {s.id for s in students}
    missing = set(payload.student_ids) - found_ids
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown student id(s): {', '.join(map(str, sorted(missing)))}",
        )

    existing_ids = {
        row for row in db.scalars(
            select(ProgramEnrollment.student_id).where(ProgramEnrollment.program_id == program_id)
        ).all()
    }
    for student in students:
        if student.id not in existing_ids:
            db.add(ProgramEnrollment(
                program_id=program.id, student_id=student.id, status=EnrollmentStatusEnum(payload.status),
            ))

    db.commit()
    return get_program(program_id, db, actor)


@router.patch("/{program_id}/enrollments/{enrollment_id}", response_model=ProgramDetailOut)
def update_enrollment(
    program_id: int,
    enrollment_id: int,
    payload: UpdateEnrollmentRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("programs.edit")),
):
    require_approved_program(db, program_id, active=False)
    enrollment = db.get(ProgramEnrollment, enrollment_id)
    if enrollment is None or enrollment.program_id != program_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Enrollment not found")

    enrollment.status = EnrollmentStatusEnum(payload.status)
    db.add(enrollment)
    db.commit()
    return get_program(program_id, db, actor)


@router.delete("/{program_id}/enrollments/{enrollment_id}", response_model=ProgramDetailOut)
def remove_enrollment(
    program_id: int,
    enrollment_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("programs.edit")),
):
    require_approved_program(db, program_id, active=False)
    enrollment = db.get(ProgramEnrollment, enrollment_id)
    if enrollment is None or enrollment.program_id != program_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Enrollment not found")

    db.delete(enrollment)
    db.commit()
    return get_program(program_id, db, actor)


@router.get("/lookup/available-students", response_model=list[dict])
def available_students(
    q: str = "",
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("programs.view")),
):
    """Backs the student search box on the enroll-students panel."""
    query = select(User).where(User.role == RoleEnum.student, User.is_active.is_(True))
    if q:
        like = f"%{q}%"
        query = query.where(or_(User.username.ilike(like), User.full_name.ilike(like)))
    students = db.scalars(query.order_by(User.full_name).limit(50)).all()
    return [{"id": s.id, "full_name": s.full_name, "username": s.username} for s in students]
