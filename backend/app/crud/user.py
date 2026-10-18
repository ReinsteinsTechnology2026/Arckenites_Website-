import re

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models.user import RoleEnum, User


def email_conflict(db: Session, email: str) -> str | None:
    """Use the existing student-provisioning identity rules for a new email login."""
    from app.models.admin_profile import AdminProfile
    from app.models.community import CommunityProfile
    from app.models.staff import StaffProfile
    from app.models.student import StudentProfile

    normalized = email.strip().lower()
    user = db.scalar(select(User).where(func.lower(func.trim(User.username)) == normalized).limit(1))
    if user is not None:
        return "student" if user.role == RoleEnum.student else "portal_login"
    profile = db.scalar(select(StudentProfile).where(
        func.lower(func.trim(StudentProfile.email)) == normalized,
    ).limit(1))
    if profile is not None:
        return "student"
    for model in (AdminProfile, StaffProfile, CommunityProfile):
        if db.scalar(select(model.user_id).where(func.lower(func.trim(model.email)) == normalized).limit(1)) is not None:
            return "portal_login"
    return None


def get_by_username(db: Session, username: str) -> User | None:
    exact = db.scalar(select(User).where(User.username == username))
    # Keep the exact path for legacy usernames and Admin accounts. Newly
    # provisioned student and trainer email logins accept case/outer-space
    # variations; the normalized unique index prevents two such identities.
    if exact is not None:
        return exact
    if "@" not in username:
        return None
    matches = db.scalars(select(User).where(
        User.role.in_((RoleEnum.student, RoleEnum.staff)),
        func.lower(func.trim(User.username)) == username.strip().lower(),
    )).all()
    # Fail closed for ambiguous historical data before the migration runs.
    return matches[0] if len(matches) == 1 else None


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
