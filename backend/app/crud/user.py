import re

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models.user import User


def get_by_username(db: Session, username: str) -> User | None:
    """Case-insensitive on purpose — student/trainer usernames are now real
    email addresses the account owner types themselves (mixed case and all),
    not the always-lowercase auto-generated ids every account used to get."""
    return db.scalar(select(User).where(func.lower(User.username) == username.strip().lower()))


def _generate_email_username(db: Session, full_name: str, fallback: str) -> str:
    """Builds the name@arckenites.com login id every account type now
    shares — e.g. "John Doe" -> johndoe@arckenites.com. A numeric suffix is
    only appended if that exact local-part is already taken by another
    account (of any role — usernames are globally unique), keeping names
    readable in the common case."""
    slug = re.sub(r"[^a-z0-9]", "", full_name.lower()) or fallback

    candidate = f"{slug}@arckenites.com"
    if get_by_username(db, candidate) is None:
        return candidate

    n = 2
    while True:
        candidate = f"{slug}{n}@arckenites.com"
        if get_by_username(db, candidate) is None:
            return candidate
        n += 1


def generate_admin_username(db: Session, full_name: str) -> str:
    """Admin-portal account login id — see _generate_email_username. Students
    and trainers instead log in with their own real email (entered by the
    admin at account creation) — only admin accounts still get an
    auto-generated name@arckenites.com id."""
    return _generate_email_username(db, full_name, "admin")


def _next_sequential_code(db: Session, table: str, column: str, prefix: str) -> str:
    """Scans existing AK-XXX-0001-style codes in `table.column` for the
    highest sequence number used so far and returns the next one, zero
    padded to 4 digits. Assigned once, at account creation — these codes
    are otherwise immutable (no edit endpoint touches them)."""
    existing = db.execute(text(f"SELECT {column} FROM {table} WHERE {column} IS NOT NULL")).scalars().all()
    max_seq = 0
    for code in existing:
        m = re.search(r"(\d+)$", code)
        if m:
            max_seq = max(max_seq, int(m.group(1)))
    return f"{prefix}{max_seq + 1:04d}"


def generate_student_code(db: Session) -> str:
    """The student's own ID (distinct from their login username) — e.g.
    AK-STU-0001. Shown on the student's own dashboard/profile."""
    return _next_sequential_code(db, "student_profiles", "student_code", "AK-STU-")


def generate_staff_code(db: Session) -> str:
    """The trainer's own ID (distinct from their login username) — e.g.
    AK-STF-0001. Shown on the trainer's own dashboard/profile."""
    return _next_sequential_code(db, "staff_profiles", "staff_code", "AK-STF-")
