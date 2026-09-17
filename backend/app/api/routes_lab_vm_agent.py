from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import require_vm_agent
from app.core.lab_vm_secrets import decrypt_rdp_password
from app.database import get_db
from app.models.lab_vm import LabVm, LabVmAccess, LabVmAccessAuditLog, LabVmAccessStatus
from app.models.user import User
from app.schemas.lab_vm import VmAgentEventRequest, VmAgentHeartbeatResponse

router = APIRouter(prefix="/lab-vm-agent", tags=["lab-vm-agent"])


@router.post("/heartbeat", response_model=VmAgentHeartbeatResponse)
def agent_heartbeat(
    db: Session = Depends(get_db),
    vm: LabVm = Depends(require_vm_agent),
):
    """The single call an agent makes every poll cycle: proves it's alive
    (agent_last_seen_at) and gets back exactly what it needs to enforce
    locally — nothing more. Computed live against expires_at, never just
    the stored `status`, so a not-yet-swept-by-the-watchdog row can't cause
    a stale 'active' answer."""
    vm.agent_last_seen_at = datetime.now(timezone.utc)
    db.add(vm)

    now = datetime.now(timezone.utc)
    access = db.scalar(
        select(LabVmAccess).where(
            LabVmAccess.vm_id == vm.id,
            LabVmAccess.status == LabVmAccessStatus.active,
            LabVmAccess.expires_at > now,
        )
    )
    db.commit()

    # The agent needs the VM's current password applied regardless of
    # whether access is active — while inactive, this is the freshly
    # rotated "nobody knows it" value the student's old grant left behind.
    current_password = decrypt_rdp_password(vm.current_agent_password_encrypted) if vm.current_agent_password_encrypted else None

    if access is None:
        return VmAgentHeartbeatResponse(status="none", student_username=vm.student_rdp_username, rdp_password=current_password)

    student = db.get(User, access.student_id)
    if not student:
        return VmAgentHeartbeatResponse(status="none", student_username=vm.student_rdp_username, rdp_password=current_password)

    return VmAgentHeartbeatResponse(
        status="active",
        student_username=vm.student_rdp_username,
        expires_at=access.expires_at,
        rdp_password=current_password,
    )


@router.post("/events", status_code=204)
def agent_event(
    payload: VmAgentEventRequest,
    db: Session = Depends(get_db),
    vm: LabVm = Depends(require_vm_agent),
):
    """Best-effort event report from the agent's own local observation
    (e.g. it saw the RDP session actually disconnect). Never trusted as
    proof of authorization by itself — it's audit trail, not a grant.

    student_id on the audit table is a required FK, so an event is only
    recorded when it can be tied to that VM's most recent access grant —
    there's no meaningful student to attribute a VM-level event to
    otherwise (e.g. an agent starting up with no grant ever issued yet),
    and the row is silently dropped rather than inventing a placeholder."""
    access = db.scalar(
        select(LabVmAccess)
        .where(LabVmAccess.vm_id == vm.id)
        .order_by(LabVmAccess.created_at.desc())
    )
    if access is not None:
        db.add(LabVmAccessAuditLog(
            student_id=access.student_id,
            vm_id=vm.id,
            action=payload.action.upper()[:30],
            performed_by=None,
            reason=payload.reason,
        ))
        db.commit()
    return None
