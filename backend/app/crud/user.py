import re

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.user import RoleEnum, User


def get_by_username(db: Session, username: str) -> User | None:
    return db.scalar(select(User).where(User.username == username))


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


def generate_username(db: Session, full_name: str, role: RoleEnum) -> str:
    """Student login id — see _generate_email_username. `role` is only
    used as the fallback slug when the name yields nothing usable."""
    return _generate_email_username(db, full_name, role.value)


def generate_admin_username(db: Session, full_name: str) -> str:
    """Admin-portal account login id — see _generate_email_username."""
    return _generate_email_username(db, full_name, "admin")


def generate_staff_username(db: Session, full_name: str) -> str:
    """Trainer/staff account login id — same name@arckenites.com base as
    every other role, but with a ".staff" tag before the domain so trainer
    logins are visually distinguishable — e.g. "Ravi Kumar" ->
    ravikumar.staff@arckenites.com. Numeric suffix (on the name part, before
    ".staff") only on collision, same as every other role."""
    slug = re.sub(r"[^a-z0-9]", "", full_name.lower()) or "trainer"

    candidate = f"{slug}.staff@arckenites.com"
    if get_by_username(db, candidate) is None:
        return candidate

    n = 2
    while True:
        candidate = f"{slug}{n}.staff@arckenites.com"
        if get_by_username(db, candidate) is None:
            return candidate
        n += 1


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
