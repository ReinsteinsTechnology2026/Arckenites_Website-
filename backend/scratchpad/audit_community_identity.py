"""READ-ONLY pre-migration audit for the Community identity uniqueness change.

Reports whether existing data would block migration d5a7c3e1f9b4 (enforce
unique Community email and normalized mobile). It only SELECTs, runs inside
a transaction the server marks READ ONLY, and always rolls back. It never
creates, alters, or drops anything, and it does not run the migration.

Normalization is NOT re-implemented here: the mobile normalizer is loaded
from the migration file itself, so the audit and the migration cannot drift.
Emails and mobile numbers are masked in the output; user ids are shown.

Usage (from the backend directory, with the server's own environment):
    python scratchpad/audit_community_identity.py
The database connection comes from app.database, i.e. the same DATABASE_URL
the application uses. It is never printed.
"""
import importlib.util
import sys
from collections import defaultdict
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import text  # noqa: E402

from app.database import engine  # noqa: E402

MIGRATION_PATH = BACKEND_DIR / "alembic" / "versions" / "d5a7c3e1f9b4_add_community_identity_uniqueness.py"


def _load_migration_normalizer():
    spec = importlib.util.spec_from_file_location("community_identity_migration", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._normalize_mobile


normalize_mobile = _load_migration_normalizer()


def mask_email(value: str) -> str:
    local, _, domain = (value or "").partition("@")
    return f"{local[:2]}***@{domain}" if domain else "***"


def mask_mobile(value: str) -> str:
    return ("*" * max(len(value) - 4, 0)) + value[-4:] if value else "(empty)"


def run_audit() -> tuple[list[str], list[str]]:
    """Returns (report_lines, blockers). Every statement is a SELECT."""
    report: list[str] = []
    blockers: list[str] = []

    with engine.connect() as conn:
        # The server itself refuses writes for the rest of this transaction.
        conn.execute(text("SET TRANSACTION READ ONLY"))
        read_only = conn.execute(text("SHOW transaction_read_only")).scalar()

        profiles = conn.execute(text(
            "SELECT p.user_id, p.mobile_number, p.email, u.username, u.role::text AS role "
            "FROM community_profiles p JOIN users u ON u.id = p.user_id ORDER BY p.user_id"
        )).all()
        community_users = conn.execute(text(
            "SELECT id, username FROM users WHERE role::text = 'community' ORDER BY id"
        )).all()

        conn.rollback()  # nothing to keep; the transaction ends unwritten

    report.append(f"Transaction read-only: {'yes' if read_only in ('on', True, 'true') else 'NO'}")
    report.append(f"Completed Community profiles: {len(profiles)}; Community users: {len(community_users)}")

    # A. Completed profiles: duplicate case-insensitive email (migration check 1)
    by_email: dict[str, list[int]] = defaultdict(list)
    for user_id, _mobile, email, _username, _role in profiles:
        by_email[(email or "").strip().lower()].append(user_id)
    report.append("\nEmail duplicates:")
    email_groups = {k: v for k, v in by_email.items() if len(v) > 1}
    if not email_groups:
        report.append("NONE")
    for key, ids in email_groups.items():
        report.append(f"- {mask_email(key)}: user ids {ids}")
        blockers.append("duplicate case-insensitive email among completed Community profiles")

    # A. Completed profiles: duplicate normalized mobile (migration check 2)
    by_mobile: dict[str, list[int]] = defaultdict(list)
    for user_id, mobile, _email, _username, _role in profiles:
        by_mobile[normalize_mobile(mobile)].append(user_id)
    report.append("\nMobile duplicates:")
    mobile_groups = {k: v for k, v in by_mobile.items() if len(v) > 1 and k}
    if not mobile_groups:
        report.append("NONE")
    for key, ids in mobile_groups.items():
        report.append(f"- {mask_mobile(key)}: user ids {ids}")
        blockers.append("duplicate normalized mobile among completed Community profiles")

    # Would collide on the unique index even without a partner row (empty digits).
    empty_mobile_ids = by_mobile.get("", [])
    if empty_mobile_ids:
        report.append(f"- mobile without any digits (normalizes to empty): user ids {empty_mobile_ids}")
        blockers.append("mobile number with no digits (normalizes to empty and collides on the unique index)")

    # B. Community usernames that collide case-insensitively (migration check 3)
    by_username: dict[str, list[int]] = defaultdict(list)
    for user_id, username in community_users:
        by_username[(username or "").strip().lower()].append(user_id)
    report.append("\nUsername duplicates:")
    username_groups = {k: v for k, v in by_username.items() if len(v) > 1}
    if not username_groups:
        report.append("NONE")
    for key, ids in username_groups.items():
        report.append(f"- {mask_email(key)}: user ids {ids}")
        blockers.append("duplicate case-insensitive username among Community users")

    # C. A profile must belong to a community user (data sanity for the same migration)
    non_community = [user_id for user_id, _m, _e, _u, role in profiles if role != "community"]
    if non_community:
        report.append(f"\nProfiles attached to a non-community user: user ids {non_community}")
        blockers.append("community profile attached to a non-community user")

    return report, blockers


def main() -> int:
    report, blockers = run_audit()
    print("PRODUCTION COMMUNITY IDENTITY AUDIT" if "--production" in sys.argv else "COMMUNITY IDENTITY AUDIT (read-only)")
    print("\n".join(report))
    print("\nMigration blockers:")
    if blockers:
        for b in sorted(set(blockers)):
            print(f"- {b}")
    else:
        print("NONE")
    print("\nMigration status: NOT RUN (this audit performs no writes)")
    return 1 if blockers else 0


if __name__ == "__main__":
    sys.exit(main())
