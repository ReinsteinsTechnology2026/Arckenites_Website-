from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.deps import require_permission
from app.core.video import generate_batch_room_name
from app.crud.audit import write_audit_event
from app.database import get_db
from app.models.audit_log import AuthEventType
from app.models.batch import Batch, BatchEnrollment, BatchStatusEnum, BatchTypeEnum
from app.models.batch_resources import ClassVideo, LabAccess, StudyMaterial
from app.models.class_session import ClassSession
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
from app.schemas.batch_resources import (
    ClassVideoOut,
    CreateClassVideoRequest,
    CreateLabAccessRequest,
    CreateStudyMaterialRequest,
    LabAccessOut,
    StudyMaterialOut,
)
from app.schemas.class_sessions import ClassSessionOut, CreateClassSessionRequest, UpdateClassSessionRequest
from app.schemas.video import VideoRoomOut

router = APIRouter(prefix="/admin/batches", tags=["admin-batches"])


def _client_meta(request: Request) -> tuple[str, str]:
    ip = request.client.host if request.client else "unknown"
    ua = request.headers.get("user-agent", "unknown")
    return ip, ua


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
def batch_stats(db: Session = Depends(get_db), _actor: User = Depends(require_permission("batches.view"))):
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
    _actor: User = Depends(require_permission("batches.view")),
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
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("batches.create")),
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

    ip, ua = _client_meta(request)
    write_audit_event(
        db, AuthEventType.batch_created, ip, ua, user=actor, detail=f"Created batch {batch.name}",
        module="batches", target=batch.name, status="success",
    )

    return _detail(_get_batch_or_404(db, batch.id))


@router.get("/{batch_id}", response_model=BatchDetailOut)
def get_batch(batch_id: int, db: Session = Depends(get_db), _actor: User = Depends(require_permission("batches.view"))):
    return _detail(_get_batch_or_404(db, batch_id))


@router.get("/{batch_id}/video", response_model=VideoRoomOut)
def batch_video_room(
    batch_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("batches.view")),
):
    """This batch's permanent video room — exists automatically the moment
    the batch does, no scheduled class required. Same HMAC formula as the
    student/trainer endpoints, so everyone lands in the identical room."""
    batch = _get_batch_or_404(db, batch_id)
    return VideoRoomOut(
        room_name=generate_batch_room_name(batch.id),
        display_name=actor.full_name,
        subject=batch.name,
    )


@router.patch("/{batch_id}", response_model=BatchDetailOut)
def update_batch(
    batch_id: int,
    payload: UpdateBatchRequest,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("batches.edit")),
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

    ip, ua = _client_meta(request)
    write_audit_event(
        db, AuthEventType.batch_updated, ip, ua, user=actor, detail=f"Updated batch {batch.name}",
        module="batches", target=batch.name, status="success",
    )

    return _detail(_get_batch_or_404(db, batch_id))


@router.delete("/{batch_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_batch(
    batch_id: int,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("batches.delete")),
):
    batch = _get_batch_or_404(db, batch_id)
    batch_name = batch.name
    db.delete(batch)  # cascades to batch_enrollments via the Batch.enrollments relationship
    db.commit()

    ip, ua = _client_meta(request)
    write_audit_event(
        db, AuthEventType.batch_deleted, ip, ua, user=actor, detail=f"Deleted batch {batch_name}",
        module="batches", target=batch_name, status="success",
    )
    return None


@router.post("/{batch_id}/students", response_model=BatchDetailOut)
def allocate_students(
    batch_id: int,
    payload: AllocateStudentsRequest,
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("batches.edit")),
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
    _actor: User = Depends(require_permission("batches.edit")),
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
    _actor: User = Depends(require_permission("batches.view")),
):
    """Backs the student search box on the allocate-students panel."""
    query = select(User).where(User.role == RoleEnum.student, User.is_active.is_(True))
    if q:
        like = f"%{q}%"
        query = query.where(or_(User.username.ilike(like), User.full_name.ilike(like)))
    students = db.scalars(query.order_by(User.full_name).limit(50)).all()
    return [{"id": s.id, "full_name": s.full_name, "username": s.username} for s in students]


def _session_out(session: ClassSession, batch_name: str) -> ClassSessionOut:
    return ClassSessionOut(
        id=session.id, batch_id=session.batch_id, batch_name=batch_name,
        title=session.title, session_date=session.session_date,
        start_time=session.start_time, end_time=session.end_time,
        meeting_link=session.meeting_link, notes=session.notes, created_at=session.created_at,
    )


@router.get("/{batch_id}/sessions", response_model=list[ClassSessionOut])
def list_sessions(batch_id: int, db: Session = Depends(get_db), _actor: User = Depends(require_permission("batches.view"))):
    batch = _get_batch_or_404(db, batch_id)
    sessions = db.scalars(
        select(ClassSession).where(ClassSession.batch_id == batch_id).order_by(ClassSession.session_date)
    ).all()
    return [_session_out(s, batch.name) for s in sessions]


@router.post("/{batch_id}/sessions", response_model=ClassSessionOut, status_code=201)
def create_session(
    batch_id: int,
    payload: CreateClassSessionRequest,
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("batches.edit")),
):
    batch = _get_batch_or_404(db, batch_id)
    session = ClassSession(
        batch_id=batch_id, title=payload.title, session_date=payload.session_date,
        start_time=payload.start_time, end_time=payload.end_time,
        meeting_link=payload.meeting_link, notes=payload.notes,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return _session_out(session, batch.name)


def _get_session_or_404(db: Session, batch_id: int, session_id: int) -> ClassSession:
    session = db.get(ClassSession, session_id)
    if session is None or session.batch_id != batch_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Class session not found")
    return session


@router.patch("/{batch_id}/sessions/{session_id}", response_model=ClassSessionOut)
def update_session(
    batch_id: int,
    session_id: int,
    payload: UpdateClassSessionRequest,
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("batches.edit")),
):
    batch = _get_batch_or_404(db, batch_id)
    session = _get_session_or_404(db, batch_id, session_id)

    if payload.title is not None:
        session.title = payload.title
    if payload.session_date is not None:
        session.session_date = payload.session_date
    if payload.start_time is not None:
        session.start_time = payload.start_time
    if payload.end_time is not None:
        session.end_time = payload.end_time
    if payload.meeting_link is not None:
        session.meeting_link = payload.meeting_link
    if payload.notes is not None:
        session.notes = payload.notes

    db.add(session)
    db.commit()
    db.refresh(session)
    return _session_out(session, batch.name)


@router.delete("/{batch_id}/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_session(
    batch_id: int,
    session_id: int,
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("batches.edit")),
):
    session = _get_session_or_404(db, batch_id, session_id)
    db.delete(session)
    db.commit()
    return None


# ---------------------------------------------------------------------------
# Lab access, class videos, study materials — same shape: a link (+ extra
# fields for lab access) posted against a batch. List/create/delete only;
# admins recreate an entry to change it rather than editing in place.
# Gated by batches.view/batches.edit (not individually audit-logged — these
# are batch content edits, not the kind of top-level action the Activity
# Logs feature tracks; see the "don't log every minor interaction" scope note).
# ---------------------------------------------------------------------------

@router.get("/{batch_id}/lab-access", response_model=list[LabAccessOut])
def list_lab_access(batch_id: int, db: Session = Depends(get_db), _actor: User = Depends(require_permission("batches.view"))):
    batch = _get_batch_or_404(db, batch_id)
    rows = db.scalars(select(LabAccess).where(LabAccess.batch_id == batch_id).order_by(LabAccess.created_at.desc())).all()
    return [LabAccessOut(
        id=r.id, batch_id=r.batch_id, batch_name=batch.name, title=r.title, access_url=r.access_url,
        username=r.username, password=r.password, notes=r.notes, created_at=r.created_at,
    ) for r in rows]


@router.post("/{batch_id}/lab-access", response_model=LabAccessOut, status_code=201)
def create_lab_access(
    batch_id: int, payload: CreateLabAccessRequest,
    db: Session = Depends(get_db), _actor: User = Depends(require_permission("batches.edit")),
):
    batch = _get_batch_or_404(db, batch_id)
    entry = LabAccess(
        batch_id=batch_id, title=payload.title, access_url=payload.access_url,
        username=payload.username, password=payload.password, notes=payload.notes,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return LabAccessOut(
        id=entry.id, batch_id=entry.batch_id, batch_name=batch.name, title=entry.title, access_url=entry.access_url,
        username=entry.username, password=entry.password, notes=entry.notes, created_at=entry.created_at,
    )


@router.delete("/{batch_id}/lab-access/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_lab_access(
    batch_id: int, entry_id: int, db: Session = Depends(get_db), _actor: User = Depends(require_permission("batches.edit")),
):
    entry = db.get(LabAccess, entry_id)
    if entry is None or entry.batch_id != batch_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lab access entry not found")
    db.delete(entry)
    db.commit()
    return None


@router.get("/{batch_id}/videos", response_model=list[ClassVideoOut])
def list_videos(batch_id: int, db: Session = Depends(get_db), _actor: User = Depends(require_permission("batches.view"))):
    batch = _get_batch_or_404(db, batch_id)
    rows = db.scalars(select(ClassVideo).where(ClassVideo.batch_id == batch_id).order_by(ClassVideo.created_at.desc())).all()
    return [ClassVideoOut(
        id=r.id, batch_id=r.batch_id, batch_name=batch.name, title=r.title,
        video_url=r.video_url, description=r.description, created_at=r.created_at,
    ) for r in rows]


@router.post("/{batch_id}/videos", response_model=ClassVideoOut, status_code=201)
def create_video(
    batch_id: int, payload: CreateClassVideoRequest,
    db: Session = Depends(get_db), _actor: User = Depends(require_permission("batches.edit")),
):
    batch = _get_batch_or_404(db, batch_id)
    entry = ClassVideo(batch_id=batch_id, title=payload.title, video_url=payload.video_url, description=payload.description)
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return ClassVideoOut(
        id=entry.id, batch_id=entry.batch_id, batch_name=batch.name, title=entry.title,
        video_url=entry.video_url, description=entry.description, created_at=entry.created_at,
    )


@router.delete("/{batch_id}/videos/{video_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_video(
    batch_id: int, video_id: int, db: Session = Depends(get_db), _actor: User = Depends(require_permission("batches.edit")),
):
    entry = db.get(ClassVideo, video_id)
    if entry is None or entry.batch_id != batch_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video not found")
    db.delete(entry)
    db.commit()
    return None


@router.get("/{batch_id}/materials", response_model=list[StudyMaterialOut])
def list_materials(batch_id: int, db: Session = Depends(get_db), _actor: User = Depends(require_permission("batches.view"))):
    batch = _get_batch_or_404(db, batch_id)
    rows = db.scalars(select(StudyMaterial).where(StudyMaterial.batch_id == batch_id).order_by(StudyMaterial.created_at.desc())).all()
    return [StudyMaterialOut(
        id=r.id, batch_id=r.batch_id, batch_name=batch.name, title=r.title,
        file_url=r.file_url, description=r.description, created_at=r.created_at,
    ) for r in rows]


@router.post("/{batch_id}/materials", response_model=StudyMaterialOut, status_code=201)
def create_material(
    batch_id: int, payload: CreateStudyMaterialRequest,
    db: Session = Depends(get_db), _actor: User = Depends(require_permission("batches.edit")),
):
    batch = _get_batch_or_404(db, batch_id)
    entry = StudyMaterial(batch_id=batch_id, title=payload.title, file_url=payload.file_url, description=payload.description)
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return StudyMaterialOut(
        id=entry.id, batch_id=entry.batch_id, batch_name=batch.name, title=entry.title,
        file_url=entry.file_url, description=entry.description, created_at=entry.created_at,
    )


@router.delete("/{batch_id}/materials/{material_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_material(
    batch_id: int, material_id: int, db: Session = Depends(get_db), _actor: User = Depends(require_permission("batches.edit")),
):
    entry = db.get(StudyMaterial, material_id)
    if entry is None or entry.batch_id != batch_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Study material not found")
    db.delete(entry)
    db.commit()
    return None
