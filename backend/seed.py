"""Phase 1 seed data.

Creates the two real bootstrap admin accounts (moved out of the old
plaintext-in-JS admin-login.js) plus one demo student and one demo staff
account used purely to verify the login -> dashboard -> logout flow for
those two roles end-to-end, since no real provisioning UI exists until
Phase 5 (Student/Staff Management). The demo accounts are clearly marked
as dev-only below and are safe to delete once real accounts exist.

Run with: python seed.py   (from the backend/ directory, venv active)
"""

from app.core.security import hash_password
from app.database import SessionLocal
from app.models.staff import StaffProfile
from app.models.student import StudentProfile
from app.models.user import RoleEnum, User
from app.config import settings


def upsert_user(db, *, username, password, full_name, role, must_change_password):
    existing = db.query(User).filter(User.username == username).first()
    if existing:
        print(f"  skip (already exists): {username}")
        return existing

    user = User(
        username=username,
        password_hash=hash_password(password),
        full_name=full_name,
        role=role,
        must_change_password=must_change_password,
    )
    db.add(user)
    db.flush()
    print(f"  created: {username} ({role.value})")
    return user


def reset_admin_password(db, *, username, password, full_name):
    """Bootstrap admins always sync to .env on every seed run, unlike the
    skip-if-exists demo accounts — this is the only way to actually rotate
    an admin credential without a provisioning UI or DB shell access."""
    user = db.query(User).filter(User.username == username).first()
    if user is None:
        user = User(username=username, full_name=full_name, role=RoleEnum.admin)
        db.add(user)
        print(f"  created: {username} (admin)")
    else:
        print(f"  reset password: {username} (admin)")

    user.password_hash = hash_password(password)
    user.must_change_password = True
    user.is_active = True
    user.failed_login_count = 0
    user.locked_until = None
    db.flush()
    return user


def main():
    db = SessionLocal()
    try:
        print("Seeding bootstrap admin accounts...")
        # These credentials previously lived in plaintext in js/admin-login.js.
        # Treat them as already-known/compromised: must_change_password=True
        # forces rotation on first real login.
        reset_admin_password(
            db,
            username=settings.seed_admin1_username,
            password=settings.seed_admin1_password,
            full_name="Admin One",
        )
        reset_admin_password(
            db,
            username=settings.seed_admin2_username,
            password=settings.seed_admin2_password,
            full_name="Admin Two",
        )

        print("Seeding dev-only demo student/staff accounts...")
        demo_student = upsert_user(
            db,
            username="student.demo",
            password="Student#Demo1",
            full_name="Demo Student",
            role=RoleEnum.student,
            must_change_password=True,
        )
        if demo_student and demo_student.student_profile is None:
            db.add(StudentProfile(user_id=demo_student.id, student_code="DEMO-STU-001"))

        demo_staff = upsert_user(
            db,
            username="staff.demo",
            password="Staff#Demo1",
            full_name="Demo Trainer",
            role=RoleEnum.staff,
            must_change_password=True,
        )
        if demo_staff and demo_staff.staff_profile is None:
            db.add(StaffProfile(user_id=demo_staff.id, staff_code="DEMO-STF-001", designation="Trainer"))

        db.commit()
        print("Done.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
