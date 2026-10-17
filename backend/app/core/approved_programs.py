"""Closed admin selection policy, resolved against real catalog records.

No IDs, aliases, renames or inserts are inferred. In particular, Official
Certification Program is not silently treated as Certificational Program.
Legacy rows stay available to historical read paths.
"""
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.program import Program, ProgramStatusEnum

APPROVED_PROGRAM_NAMES = (
    "Institutional Program",
    "Freshers Job Hack Program",
    "Certificational Program",
)
_LABELS = {name.lower(): name for name in APPROVED_PROGRAM_NAMES}


def approved_name(name: str) -> str | None:
    return _LABELS.get(name.strip().lower())


@dataclass
class ApprovedCatalog:
    programs: list[Program]
    missing: list[str]
    ambiguous: list[str]

    @property
    def active(self) -> list[Program]:
        return [p for p in self.programs if p.status == ProgramStatusEnum.active]

    @property
    def inactive(self) -> list[str]:
        return [approved_name(p.name) for p in self.programs if p.status != ProgramStatusEnum.active]

    @property
    def notice(self) -> str | None:
        parts = []
        if self.missing:
            parts.append("Approved programs missing from the catalog: " + "; ".join(self.missing) + ".")
        if self.ambiguous:
            parts.append("Multiple catalog records match: " + "; ".join(self.ambiguous) + ". Resolve the canonical records before selecting them.")
        if self.inactive:
            parts.append("Approved programs currently inactive: " + "; ".join(self.inactive) + ".")
        if parts:
            parts.append("Only existing, unambiguous, active approved programs can be assigned. No records have been created or moved.")
        return " ".join(parts) or None

    def options(self) -> list[dict]:
        return [{"id": p.id, "slug": p.slug, "name": approved_name(p.name)} for p in self.active]


def resolve_approved_catalog(db: Session) -> ApprovedCatalog:
    rows = db.scalars(select(Program).where(
        func.lower(func.trim(Program.name)).in_(_LABELS),
    ).order_by(Program.id)).all()
    matches = {name: [] for name in APPROVED_PROGRAM_NAMES}
    for row in rows:
        label = approved_name(row.name)
        if label:
            matches[label].append(row)
    return ApprovedCatalog(
        programs=[rows[0] for rows in matches.values() if len(rows) == 1],
        missing=[label for label, rows in matches.items() if not rows],
        ambiguous=[label for label, rows in matches.items() if len(rows) > 1],
    )


def require_approved_program(db: Session, program_id: int, *, active: bool = True) -> Program:
    catalog = resolve_approved_catalog(db)
    program = next((p for p in catalog.programs if p.id == program_id), None)
    if program is None:
        raise HTTPException(
            status_code=400,
            detail="Please select an existing approved program: " + "; ".join(APPROVED_PROGRAM_NAMES) + ". "
                   + (catalog.notice or "Other programs are retained for history only."),
        )
    if active and program.status != ProgramStatusEnum.active:
        raise HTTPException(status_code=400, detail="Please select an active approved program.")
    return program
