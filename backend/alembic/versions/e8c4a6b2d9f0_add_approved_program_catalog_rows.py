"""Add missing approved program catalog rows without changing existing rows.

Revision ID: e8c4a6b2d9f0
Revises: d7f9b3c5e2a1
"""

from alembic import op
import sqlalchemy as sa


revision = "e8c4a6b2d9f0"
down_revision = "d7f9b3c5e2a1"
branch_labels = None
depends_on = None


# Names must match app.core.approved_programs.APPROVED_PROGRAM_NAMES. The
# existing catalog migration already supplies Institutional Program.
_PROGRAMS = (
    ("Freshers Job Hack Program", "freshers_job_hack", "FJH-01", "Placement"),
    ("Certificational Program", "certificational", "CFP-01", "Certification"),
)


def _normalized(value: str | None) -> str:
    return " ".join((value or "").split()).casefold()


def upgrade() -> None:
    bind = op.get_bind()
    # The Admin catalog API uses this same transaction lock. A concurrent
    # writer cannot make the preflight stale between inspection and insert.
    bind.execute(sa.text("SELECT pg_advisory_xact_lock(19041702)"))
    rows = bind.execute(sa.text("SELECT id, name, slug, code, status FROM programs ORDER BY id")).mappings().all()

    institutional = [row for row in rows if _normalized(row["name"]) == "institutional program"]
    if len(institutional) != 1 or institutional[0]["status"] != "active":
        raise RuntimeError("Approved catalog migration requires one active Institutional Program; no rows changed")

    to_insert = []
    for name, slug, code, category in _PROGRAMS:
        matches = [row for row in rows if _normalized(row["name"]) == _normalized(name)]
        if len(matches) > 1:
            raise RuntimeError(f"Ambiguous approved program name: {name}; no rows changed")
        matching_slug = [row for row in rows if _normalized(row["slug"]) == _normalized(slug)]
        matching_code = [row for row in rows if _normalized(row["code"]) == _normalized(code)]
        if len(matching_slug) > 1 or len(matching_code) > 1:
            raise RuntimeError(f"Ambiguous slug or code for {name}; no rows changed")
        if matches:
            existing = matches[0]
            if (existing["status"] != "active"
                    or _normalized(existing["slug"]) != _normalized(slug)
                    or _normalized(existing["code"]) != _normalized(code)):
                raise RuntimeError(f"Existing approved program identity differs: {name}; no rows changed")
            if matching_slug != matches or matching_code != matches:
                raise RuntimeError(f"Slug or code conflicts with another record: {name}; no rows changed")
        else:
            if matching_slug or matching_code:
                raise RuntimeError(f"Slug or code belongs to another record: {name}; no rows changed")
            to_insert.append((name, slug, code, category))

    for name, slug, code, category in to_insert:
        bind.execute(sa.text("""
            INSERT INTO programs (name, slug, code, category, mode, status)
            VALUES (:name, :slug, :code, :category, 'offline', 'active')
        """), {"name": name, "slug": slug, "code": code, "category": category})


def downgrade() -> None:
    # Intentional no-op. These catalog rows may acquire enrollments or batches
    # immediately after release, so a schema downgrade must never delete them.
    # Re-running upgrade() afterwards is safe: it recognises the canonical rows
    # and inserts nothing. Remove the rows only by a reviewed manual data
    # rollback or a restored backup.
    pass
