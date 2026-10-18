"""Explicitly repair missing approved labels in a marked disposable QA DB.

This is a rehearsal, not a production migration. It never chooses IDs or
modifies existing rows. Production catalog changes require separate review.
"""

import os

from sqlalchemy.orm import Session

from app.core.approved_programs import resolve_approved_catalog
from app.database import engine
from app.models.program import Program, ProgramStatusEnum
from tests.postgres_safety import isolated_postgres_enabled


QA_SLUGS = {
    "Freshers Job Hack Program": "freshers_job_hack",
    "Certificational Program": "certificational",
}


def main() -> None:
    if os.getenv("ARCK_QA_PROGRAM_REPAIR") != "1" or not isolated_postgres_enabled():
        raise SystemExit("Refusing catalog write outside an explicitly marked disposable QA database")
    with Session(bind=engine) as db:
        catalog = resolve_approved_catalog(db)
        if catalog.ambiguous or catalog.inactive:
            raise SystemExit(f"Review ambiguous or inactive approved records first: {catalog.notice}")
        unknown_missing = set(catalog.missing) - set(QA_SLUGS)
        if unknown_missing:
            raise SystemExit(f"Missing approved records need individual review: {sorted(unknown_missing)}")
        existing_slugs = {row.slug for row in db.query(Program).all()}
        for name in catalog.missing:
            slug = QA_SLUGS[name]
            if slug in existing_slugs:
                raise SystemExit(f"Slug {slug} already belongs to another record; no changes committed")
            db.add(Program(slug=slug, name=name, status=ProgramStatusEnum.active))
        db.flush()
        final = resolve_approved_catalog(db)
        if final.missing or final.ambiguous or final.inactive:
            raise SystemExit(f"Catalog repair did not resolve approved options: {final.notice}")
        db.commit()
        print("QA approved program records:", [(row.id, row.name, row.status.value) for row in final.programs])


if __name__ == "__main__":
    main()
