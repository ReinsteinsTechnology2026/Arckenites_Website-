import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import RoleEnum, User


def get_by_username(db: Session, username: str) -> User | None:
    return db.scalar(select(User).where(User.username == username))


def generate_username(db: Session, full_name: str, role: RoleEnum) -> str:
    """Builds the ak{name}@{seq} login id described to admins when they
    provision a student or staff account — e.g. "John Doe" -> akjohndoe@004,
    where 004 is one past the highest sequence number used by any account of
    that same role so far (not per-name), so every new account gets the next
    running number for its role regardless of what name they were given."""
    slug = re.sub(r"[^a-z0-9]", "", full_name.lower())
    if not slug:
        slug = role.value

    existing_usernames = db.scalars(select(User.username).where(User.role == role)).all()
    max_seq = 0
    for uname in existing_usernames:
        match = re.search(r"@(\d+)$", uname)
        if match:
            max_seq = max(max_seq, int(match.group(1)))

    for seq in range(max_seq + 1, max_seq + 1000):
        candidate = f"ak{slug}@{seq:03d}"
        if get_by_username(db, candidate) is None:
            return candidate

    raise ValueError(f"No available username slots for '{full_name}'")


def generate_admin_username(db: Session, full_name: str) -> str:
    """Builds the AKA@Name login id for admin-portal accounts — same style
    as generate_staff_username's AKT@Name for trainers, just a different
    prefix so the two account types stay visually distinguishable."""
    words = re.findall(r"[A-Za-z0-9]+", full_name)
    name_part = "".join(w[:1].upper() + w[1:] for w in words) if words else "Admin"

    candidate = f"AKA@{name_part}"
    if get_by_username(db, candidate) is None:
        return candidate

    n = 2
    while True:
        candidate = f"AKA@{name_part}{n}"
        if get_by_username(db, candidate) is None:
            return candidate
        n += 1


def generate_staff_username(db: Session, full_name: str) -> str:
    """Builds the AKT@Name login id for trainers, across every program —
    e.g. "Sheriff" -> AKT@Sheriff, "Madhan Kumar" -> AKT@MadhanKumar. Unlike
    students, there's no running sequence number in the common case; a
    numeric suffix is only added if that exact name is already taken by
    another trainer, to keep names readable."""
    words = re.findall(r"[A-Za-z0-9]+", full_name)
    name_part = "".join(w[:1].upper() + w[1:] for w in words) if words else "Trainer"

    candidate = f"AKT@{name_part}"
    if get_by_username(db, candidate) is None:
        return candidate

    n = 2
    while True:
        candidate = f"AKT@{name_part}{n}"
        if get_by_username(db, candidate) is None:
            return candidate
        n += 1
