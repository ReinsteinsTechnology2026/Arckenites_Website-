from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.admin_role import AdminRolePermission, SUPER_ADMIN_SLUG
from app.models.user import RoleEnum, User

# Static catalog — the single source of truth for every permission key that
# exists in the system. Seeded into the `permissions` table by seed.py.
# Modules are limited to areas that actually have a real backend route today
# (no "courses.*" — no courses module exists; Programs covers that ground).
PERMISSION_CATALOG: list[dict] = [
    {"key": "students.view", "module": "Students", "action": "view", "description": "View student accounts."},
    {"key": "students.create", "module": "Students", "action": "create", "description": "Create student accounts."},
    {"key": "students.edit", "module": "Students", "action": "edit", "description": "Edit students, including enable/disable and password reset."},
    {"key": "students.delete", "module": "Students", "action": "delete", "description": "Delete student accounts."},
    {"key": "students.export", "module": "Students", "action": "export", "description": "Export student data."},

    {"key": "trainers.view", "module": "Trainers", "action": "view", "description": "View trainer accounts."},
    {"key": "trainers.create", "module": "Trainers", "action": "create", "description": "Create trainer accounts."},
    {"key": "trainers.edit", "module": "Trainers", "action": "edit", "description": "Edit trainers, including enable/disable and permissions."},
    {"key": "trainers.delete", "module": "Trainers", "action": "delete", "description": "Delete trainer accounts."},
    {"key": "trainers.reset_password", "module": "Trainers", "action": "reset_password", "description": "Reset a trainer's password."},

    {"key": "programs.view", "module": "Programs", "action": "view", "description": "View programs."},
    {"key": "programs.create", "module": "Programs", "action": "create", "description": "Create programs."},
    {"key": "programs.edit", "module": "Programs", "action": "edit", "description": "Edit programs."},
    {"key": "programs.delete", "module": "Programs", "action": "delete", "description": "Delete programs."},

    {"key": "batches.view", "module": "Batches", "action": "view", "description": "View batches and their class schedule."},
    {"key": "batches.create", "module": "Batches", "action": "create", "description": "Create batches."},
    {"key": "batches.edit", "module": "Batches", "action": "edit", "description": "Edit batches, including scheduling classes."},
    {"key": "batches.delete", "module": "Batches", "action": "delete", "description": "Delete batches."},

    {"key": "lab_access.view", "module": "Lab Access", "action": "view", "description": "View per-student lab slot bookings and lock/unlock status."},
    {"key": "lab_access.manage", "module": "Lab Access", "action": "manage", "description": "Manually lock or unlock a student's lab access."},

    {"key": "placement.view", "module": "Placement", "action": "view", "description": "View placement interviews."},
    {"key": "placement.create", "module": "Placement", "action": "create", "description": "Schedule placement interviews."},
    {"key": "placement.edit", "module": "Placement", "action": "edit", "description": "Edit placement interviews."},
    {"key": "placement.delete", "module": "Placement", "action": "delete", "description": "Delete placement interviews."},

    {"key": "admin_users.view", "module": "Admin Users", "action": "view", "description": "View admin accounts."},
    {"key": "admin_users.create", "module": "Admin Users", "action": "create", "description": "Create admin accounts."},
    {"key": "admin_users.edit", "module": "Admin Users", "action": "edit", "description": "Edit admins, including enable/disable, role, and password reset."},
    {"key": "admin_users.delete", "module": "Admin Users", "action": "delete", "description": "Delete admin accounts."},

    {"key": "roles.view", "module": "Roles", "action": "view", "description": "View roles and permissions."},
    {"key": "roles.create", "module": "Roles", "action": "create", "description": "Create custom roles."},
    {"key": "roles.edit", "module": "Roles", "action": "edit", "description": "Edit role permission grants."},
    {"key": "roles.delete", "module": "Roles", "action": "delete", "description": "Delete custom roles."},

    {"key": "activity_logs.view", "module": "Activity Logs", "action": "view", "description": "View the audit trail."},
    {"key": "activity_logs.export", "module": "Activity Logs", "action": "export", "description": "Export activity logs as CSV."},

    {"key": "settings.view", "module": "Settings", "action": "view", "description": "View system settings."},
    {"key": "settings.edit", "module": "Settings", "action": "edit", "description": "Edit system settings."},

    {"key": "support.view", "module": "Support", "action": "view", "description": "View support tickets."},
    {"key": "support.reply", "module": "Support", "action": "reply", "description": "Reply to a student on a support ticket."},
    {"key": "support.assign", "module": "Support", "action": "assign", "description": "Assign a support ticket to an admin."},
    {"key": "support.change_status", "module": "Support", "action": "change_status", "description": "Change a support ticket's status, including reopening."},
    {"key": "support.change_priority", "module": "Support", "action": "change_priority", "description": "Change a support ticket's priority."},
    {"key": "support.add_internal_note", "module": "Support", "action": "add_internal_note", "description": "Add an internal note, hidden from the student, to a support ticket."},
    {"key": "support.close", "module": "Support", "action": "close", "description": "Close a support ticket."},
    {"key": "support.delete", "module": "Support", "action": "delete", "description": "Delete a support ticket."},
    {"key": "support.export", "module": "Support", "action": "export", "description": "Export support ticket data."},
]

PERMISSION_KEYS: frozenset[str] = frozenset(p["key"] for p in PERMISSION_CATALOG)

_OPERATIONAL_MODULES = ["students", "trainers", "programs", "batches", "placement", "lab_access"]

# Default grants for the two editable system roles. Super Admin is not here —
# its access is computed (see user_has_permission), never read from this table.
DEFAULT_GRANTS: dict[str, list[str]] = {
    "admin": [k for k in PERMISSION_KEYS if k.split(".")[0] in _OPERATIONAL_MODULES]
    + ["activity_logs.view"]
    + [k for k in PERMISSION_KEYS if k.split(".")[0] == "support"],
    # Support Admin gets enough to actually do support work — view, reply,
    # move a ticket through the status workflow — but not the
    # higher-trust actions (assign/close/delete/export/internal notes),
    # which stay Admin/Super-Admin-only.
    "support_admin": [f"{m}.view" for m in _OPERATIONAL_MODULES] + ["support.view", "support.reply", "support.change_status"],
}


def get_effective_permissions(db: Session, user: User) -> list[str]:
    """Every permission key the user currently holds. Empty list for
    non-admin roles and for admins with no admin_role assigned yet."""
    if user.role != RoleEnum.admin or user.admin_role is None:
        return []
    if user.admin_role.slug == SUPER_ADMIN_SLUG:
        return sorted(PERMISSION_KEYS)
    rows = db.scalars(
        select(AdminRolePermission.permission_key).where(AdminRolePermission.admin_role_id == user.admin_role_id)
    ).all()
    return sorted(rows)


def user_has_permission(db: Session, user: User, permission_key: str) -> bool:
    if user.role != RoleEnum.admin or user.admin_role is None:
        return False
    if user.admin_role.slug == SUPER_ADMIN_SLUG:
        return True
    exists = db.scalar(
        select(AdminRolePermission.id).where(
            AdminRolePermission.admin_role_id == user.admin_role_id,
            AdminRolePermission.permission_key == permission_key,
        )
    )
    return exists is not None
