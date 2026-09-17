import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.deps import hash_vm_agent_token, require_permission
from app.core.lab_vm_secrets import encrypt_rdp_password, generate_windows_compliant_password
from app.database import get_db
from app.models.lab_vm import LabVm, LabVmAccess, LabVmAccessAuditLog, LabVmAccessStatus
from app.models.user import RoleEnum, User
from app.schemas.lab_vm import (
    CreateLabVmRequest,
    GrantLabVmAccessRequest,
    LabVmAccessOut,
    LabVmAuditEntryOut,
    LabVmCreatedOut,
    LabVmOut,
    RevokeLabVmAccessRequest,
    UpdateLabVmRequest,
)

logger = logging.getLogger("lab_vm")

router = APIRouter(prefix="/admin", tags=["admin-lab-vm"])

# An agent that hasn't checked in within this many seconds is shown as
# "offline" — computed on read, never trusted as a stored flag, since a VM
# could go dark between checks with no way for the backend to know except
# "it's been quiet too long."
AGENT_OFFLINE_THRESHOLD_SEC = 90


def _agent_status(vm: LabVm) -> str:
    if vm.agent_last_seen_at is None:
        return "never_connected"
    age = (datetime.now(timezone.utc) - vm.agent_last_seen_at).total_seconds()
    return "online" if age <= AGENT_OFFLINE_THRESHOLD_SEC else "offline"


def _vm_out(vm: LabVm) -> LabVmOut:
    return LabVmOut(
        id=vm.id, name=vm.name, hostname=vm.hostname, rdp_port=vm.rdp_port,
        operating_system=vm.operating_system, description=vm.description,
        student_rdp_username=vm.student_rdp_username, is_active=vm.is_active,
        agent_status=_agent_status(vm), agent_last_seen_at=vm.agent_last_seen_at,
        created_at=vm.created_at, updated_at=vm.updated_at,
    )


def _get_vm_or_404(db: Session, vm_id: int) -> LabVm:
    vm = db.get(LabVm, vm_id)
    if vm is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="VM not found")
    return vm


def _get_student_or_404(db: Session, student_id: int) -> User:
    user = db.get(User, student_id)
    if user is None or user.role != RoleEnum.student:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found")
    return user


# ---------------------------------------------------------------------------
# VM inventory
# ---------------------------------------------------------------------------

@router.get("/lab-vms", response_model=list[LabVmOut])
def list_lab_vms(db: Session = Depends(get_db), _actor: User = Depends(require_permission("lab_vm.view"))):
    vms = db.scalars(select(LabVm).order_by(LabVm.name)).all()
    return [_vm_out(vm) for vm in vms]


@router.post("/lab-vms", response_model=LabVmCreatedOut, status_code=201)
def create_lab_vm(
    payload: CreateLabVmRequest,
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("lab_vm.manage_vms")),
):
    token = secrets.token_urlsafe(32)
    vm = LabVm(
        name=payload.name, hostname=payload.hostname, rdp_port=payload.rdp_port,
        operating_system=payload.operating_system, description=payload.description,
        student_rdp_username=payload.student_rdp_username,
        agent_token_hash=hash_vm_agent_token(token),
    )
    db.add(vm)
    db.commit()
    db.refresh(vm)
    return LabVmCreatedOut(**_vm_out(vm).model_dump(), agent_token=token)


@router.patch("/lab-vms/{vm_id}", response_model=LabVmOut)
def update_lab_vm(
    vm_id: int,
    payload: UpdateLabVmRequest,
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("lab_vm.manage_vms")),
):
    vm = _get_vm_or_404(db, vm_id)
    if payload.name is not None:
        vm.name = payload.name
    if payload.hostname is not None:
        vm.hostname = payload.hostname
    if payload.rdp_port is not None:
        vm.rdp_port = payload.rdp_port
    if payload.operating_system is not None:
        vm.operating_system = payload.operating_system or None
    if payload.description is not None:
        vm.description = payload.description or None
    if payload.student_rdp_username is not None:
        vm.student_rdp_username = payload.student_rdp_username
    if payload.is_active is not None:
        vm.is_active = payload.is_active

    db.add(vm)
    db.commit()
    db.refresh(vm)
    return _vm_out(vm)


@router.post("/lab-vms/{vm_id}/regenerate-token", response_model=LabVmCreatedOut)
def regenerate_lab_vm_token(
    vm_id: int,
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("lab_vm.manage_vms")),
):
    """Invalidates the old token immediately — the agent on that VM must be
    reconfigured with the new one before its next heartbeat will succeed."""
    vm = _get_vm_or_404(db, vm_id)
    token = secrets.token_urlsafe(32)
    vm.agent_token_hash = hash_vm_agent_token(token)
    db.add(vm)
    db.commit()
    db.refresh(vm)
    return LabVmCreatedOut(**_vm_out(vm).model_dump(), agent_token=token)


@router.delete("/lab-vms/{vm_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_lab_vm(
    vm_id: int,
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("lab_vm.manage_vms")),
):
    vm = _get_vm_or_404(db, vm_id)

    active_count = db.scalar(
        select(LabVmAccess).where(LabVmAccess.vm_id == vm.id, LabVmAccess.status == LabVmAccessStatus.active)
    )
    if active_count is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This VM has an active access grant. Revoke it first.",
        )

    # Detach (don't delete) audit history so it survives the VM being
    # removed from inventory — vm_id is nullable on the audit table for
    # exactly this reason.
    db.query(LabVmAccessAuditLog).filter(LabVmAccessAuditLog.vm_id == vm.id).update({"vm_id": None})
    db.query(LabVmAccess).filter(LabVmAccess.vm_id == vm.id).delete()
    db.delete(vm)
    db.commit()
    return None


# ---------------------------------------------------------------------------
# Access grant/revoke/list
# ---------------------------------------------------------------------------

@router.get("/lab-vm-access", response_model=list[LabVmAccessOut])
def list_lab_vm_access(db: Session = Depends(get_db), _actor: User = Depends(require_permission("lab_vm.view"))):
    rows = db.scalars(
        select(LabVmAccess)
        .options(selectinload(LabVmAccess.student), selectinload(LabVmAccess.vm))
        .order_by(LabVmAccess.created_at.desc())
    ).all()
    granted_by_ids = {r.granted_by for r in rows} | {r.revoked_by for r in rows if r.revoked_by}
    users_by_id = {u.id: u for u in db.scalars(select(User).where(User.id.in_(granted_by_ids))).all()} if granted_by_ids else {}

    return [
        LabVmAccessOut(
            id=r.id, student_id=r.student_id, student_name=r.student.full_name,
            vm_id=r.vm_id, vm_name=r.vm.name, status=r.status.value,
            granted_at=r.granted_at, expires_at=r.expires_at,
            granted_by_name=users_by_id[r.granted_by].full_name if r.granted_by in users_by_id else None,
            revoked_at=r.revoked_at,
            revoked_by_name=users_by_id[r.revoked_by].full_name if r.revoked_by in users_by_id else None,
        )
        for r in rows
    ]


@router.post("/lab-vm-access/{student_id}/grant", response_model=LabVmAccessOut)
def grant_lab_vm_access(
    student_id: int,
    payload: GrantLabVmAccessRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("lab_vm.grant")),
):
    student = _get_student_or_404(db, student_id)
    vm = _get_vm_or_404(db, payload.vm_id)
    if not vm.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This VM is not active.")

    now = datetime.now(timezone.utc)

    # Re-granting supersedes any existing active row for this student
    # (regardless of which VM it was for) rather than allowing two active
    # rows to exist — "granting again starts a fresh 2-hour period" from
    # the spec, not "adds a second grant." The partial unique index on
    # lab_vm_access is the DB-level backstop if this ever raced.
    existing_active = db.scalar(
        select(LabVmAccess).where(LabVmAccess.student_id == student_id, LabVmAccess.status == LabVmAccessStatus.active)
    )
    if existing_active is not None:
        existing_active.status = LabVmAccessStatus.revoked
        existing_active.revoked_at = now
        existing_active.revoked_by = actor.id
        existing_active.rdp_password_encrypted = None
        db.add(existing_active)
        db.add(LabVmAccessAuditLog(
            student_id=student_id, vm_id=existing_active.vm_id, action="REVOKED",
            performed_by=actor.id, reason="Superseded by new grant",
        ))
        # Rotate away the password on whichever VM this superseded grant was
        # for — it may be a different VM than the one being granted below.
        old_vm = db.get(LabVm, existing_active.vm_id)
        if old_vm is not None:
            old_vm.current_agent_password_encrypted = encrypt_rdp_password(generate_windows_compliant_password())
            db.add(old_vm)

    # A fresh, random password for this specific grant — never the VM's real
    # Administrator password. The agent applies it to student_rdp_username's
    # account on its next heartbeat; it's shown to the student only while
    # this grant stays active, and rotated away the instant it doesn't.
    new_password = generate_windows_compliant_password()
    vm.current_agent_password_encrypted = encrypt_rdp_password(new_password)
    db.add(vm)

    access = LabVmAccess(
        student_id=student_id, vm_id=vm.id, status=LabVmAccessStatus.active,
        granted_at=now, expires_at=now + timedelta(minutes=payload.duration_minutes),
        granted_by=actor.id, rdp_password_encrypted=encrypt_rdp_password(new_password),
    )
    db.add(access)
    db.add(LabVmAccessAuditLog(student_id=student_id, vm_id=vm.id, action="GRANTED", performed_by=actor.id))

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        logger.exception("Failed to grant lab VM access for student_id=%s", student_id)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Could not grant access — this student may already have an active grant. Please retry.",
        )
    db.refresh(access)

    return LabVmAccessOut(
        id=access.id, student_id=student_id, student_name=student.full_name,
        vm_id=vm.id, vm_name=vm.name, status=access.status.value,
        granted_at=access.granted_at, expires_at=access.expires_at,
        granted_by_name=actor.full_name, revoked_at=None, revoked_by_name=None,
    )


@router.post("/lab-vm-access/{student_id}/revoke", response_model=LabVmAccessOut)
def revoke_lab_vm_access(
    student_id: int,
    payload: RevokeLabVmAccessRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("lab_vm.revoke")),
):
    student = _get_student_or_404(db, student_id)
    access = db.scalar(
        select(LabVmAccess).where(LabVmAccess.student_id == student_id, LabVmAccess.status == LabVmAccessStatus.active)
    )
    if access is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This student has no active VM access.")

    now = datetime.now(timezone.utc)
    access.status = LabVmAccessStatus.revoked
    access.revoked_at = now
    access.revoked_by = actor.id
    access.rdp_password_encrypted = None
    db.add(access)
    db.add(LabVmAccessAuditLog(
        student_id=student_id, vm_id=access.vm_id, action="REVOKED",
        performed_by=actor.id, reason=payload.reason,
    ))

    vm = db.get(LabVm, access.vm_id)
    if vm is not None:
        # Rotate the VM's account password away immediately — this is what
        # makes "revoke" actually final even if the student wrote the
        # password down; group-membership removal (done by the agent on its
        # next heartbeat) is the primary control, this is the backstop.
        vm.current_agent_password_encrypted = encrypt_rdp_password(generate_windows_compliant_password())
        db.add(vm)

    db.commit()
    db.refresh(access)
    return LabVmAccessOut(
        id=access.id, student_id=student_id, student_name=student.full_name,
        vm_id=access.vm_id, vm_name=vm.name if vm else "—", status=access.status.value,
        granted_at=access.granted_at, expires_at=access.expires_at,
        granted_by_name=None, revoked_at=access.revoked_at, revoked_by_name=actor.full_name,
    )


@router.get("/lab-vm-access/audit", response_model=list[LabVmAuditEntryOut])
def lab_vm_access_audit(db: Session = Depends(get_db), _actor: User = Depends(require_permission("lab_vm.view_history"))):
    rows = db.scalars(select(LabVmAccessAuditLog).order_by(LabVmAccessAuditLog.created_at.desc()).limit(500)).all()
    student_ids = {r.student_id for r in rows}
    performer_ids = {r.performed_by for r in rows if r.performed_by}
    vm_ids = {r.vm_id for r in rows if r.vm_id}

    students = {u.id: u for u in db.scalars(select(User).where(User.id.in_(student_ids))).all()} if student_ids else {}
    performers = {u.id: u for u in db.scalars(select(User).where(User.id.in_(performer_ids))).all()} if performer_ids else {}
    vms = {v.id: v for v in db.scalars(select(LabVm).where(LabVm.id.in_(vm_ids))).all()} if vm_ids else {}

    return [
        LabVmAuditEntryOut(
            id=r.id, student_id=r.student_id,
            student_name=students[r.student_id].full_name if r.student_id in students else "—",
            vm_id=r.vm_id, vm_name=vms[r.vm_id].name if r.vm_id in vms else None,
            action=r.action,
            performed_by_name=performers[r.performed_by].full_name if r.performed_by in performers else None,
            reason=r.reason, created_at=r.created_at,
        )
        for r in rows
    ]
