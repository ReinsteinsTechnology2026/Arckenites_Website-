"""Server-side expiration for VM Lab Access — the whole point of this file
is that expiration must NOT depend on a student's browser being open, a
page being refreshed, or the Windows Agent's own clock. This loop is the
single source of truth that flips an access grant from 'active' to
'expired' once its expires_at has passed, independent of anyone looking at
it.

This does not replace the live expires_at check every read already does
(routes_admin_lab_vm.py, routes_lab_vm_agent.py, routes_students.py all
compare expires_at to now() directly, not just trust the stored status) —
that's what makes an access decision correct even in the few seconds
before this sweep next runs. This loop exists so the stored `status`
column (and the EXPIRED audit trail, and the expiry notification email)
stay accurate for anyone just reading history, without requiring a live
request to trigger the transition.

No scheduler library exists in this project (confirmed — see the VM Lab
Access plan). This is a single uvicorn process, the same assumption
ws_manager.py and rate_limit.py already document for themselves, so a
plain asyncio background task started at app startup is the reliable
option here, not a workaround.
"""

import asyncio
import logging
from datetime import datetime, timezone

from app.core.lab_vm_secrets import encrypt_rdp_password, generate_windows_compliant_password
from app.core.notify import notify_lab_vm_access_change
from app.database import SessionLocal
from app.models.lab_vm import LabVm, LabVmAccess, LabVmAccessAuditLog, LabVmAccessStatus
from app.models.user import User

logger = logging.getLogger("lab_vm_watchdog")


def sweep_expired_lab_vm_access_once() -> int:
    """One pass: expire every 'active' row whose expires_at has passed.
    Returns the number of rows expired. Synchronous — called from the
    async loop below via a plain function call, since SQLAlchemy's ORM
    session here is the same sync Session used everywhere else in this
    codebase (no async DB driver in use)."""
    db = SessionLocal()
    expired_count = 0
    try:
        now = datetime.now(timezone.utc)
        rows = db.query(LabVmAccess).filter(
            LabVmAccess.status == LabVmAccessStatus.active,
            LabVmAccess.expires_at < now,
        ).all()

        for row in rows:
            row.status = LabVmAccessStatus.expired
            row.rdp_password_encrypted = None
            db.add(row)
            db.add(LabVmAccessAuditLog(
                student_id=row.student_id,
                vm_id=row.vm_id,
                action="EXPIRED",
                performed_by=None,
                reason=None,
            ))
            # Rotate the VM's account password away the moment its grant
            # expires — the same backstop applied on explicit revoke, so a
            # student who wrote the password down can't reuse it after
            # their time is up, independent of the agent's own group-
            # membership enforcement.
            vm = db.get(LabVm, row.vm_id)
            if vm is not None:
                vm.current_agent_password_encrypted = encrypt_rdp_password(generate_windows_compliant_password())
                db.add(vm)
            expired_count += 1

        if expired_count:
            db.commit()

            # Best-effort notification, after the state change is already
            # safely committed — a failed email must never roll back or
            # block the actual expiration.
            for row in rows:
                try:
                    student = db.get(User, row.student_id)
                    vm = db.get(LabVm, row.vm_id)
                    if student and vm:
                        notify_lab_vm_access_change(student, vm.name, "Your VM lab access has expired.")
                except Exception:
                    logger.exception("Failed to send expiry notification for lab_vm_access id=%s", row.id)
        return expired_count
    except Exception:
        db.rollback()
        logger.exception("lab_vm_watchdog sweep failed")
        return 0
    finally:
        db.close()


async def run_lab_vm_watchdog(interval_sec: int = 30) -> None:
    """Runs forever in the background, started once from main.py's startup
    event. Each iteration runs the sync sweep in a worker thread
    (asyncio.to_thread) so a slow DB round-trip never blocks the event
    loop that's also serving live requests."""
    while True:
        try:
            count = await asyncio.to_thread(sweep_expired_lab_vm_access_once)
            if count:
                logger.info("lab_vm_watchdog expired %s access grant(s)", count)
        except Exception:
            logger.exception("lab_vm_watchdog iteration crashed — will retry next interval")
        await asyncio.sleep(interval_sec)
