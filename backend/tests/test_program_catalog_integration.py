"""Catalog write serialization on a marked disposable PostgreSQL database."""

import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.api.routes_admin_programs import create_program
from app.database import engine
from app.models.audit_log import AuthAuditLog
from app.models.program import Program
from app.models.user import User
from app.schemas.admin_programs import CreateProgramRequest
from tests.postgres_safety import isolated_postgres_enabled


@unittest.skipUnless(isolated_postgres_enabled(), "Requires marked disposable PostgreSQL")
class ProgramCatalogPostgresTests(unittest.TestCase):
    def test_parallel_normalized_duplicate_cannot_create_two_programs(self):
        with Session(bind=engine) as db:
            actor = db.scalar(select(User).where(User.username == "admin1@arckenites.com"))
            self.assertIsNotNone(actor)
            actor_id = actor.id
        label = f"QA Catalog {uuid4().hex[:12]}"
        barrier = Barrier(2)
        scope = {"type": "http", "method": "POST", "path": "/api/admin/programs",
                 "headers": [], "client": ("127.0.0.1", 0), "query_string": b""}

        def attempt(name):
            with Session(bind=engine) as db:
                actor = db.get(User, actor_id)
                barrier.wait(timeout=10)
                try:
                    result = create_program(CreateProgramRequest(name=name), Request(scope), db=db, actor=actor)
                    return "created", result.id
                except HTTPException as exc:
                    return "rejected", exc.status_code

        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = [future.result(timeout=30) for future in (
                    pool.submit(attempt, label), pool.submit(attempt, "  " + label.upper().replace(" ", "   ") + "  "))]
            self.assertCountEqual([kind for kind, _ in results], ["created", "rejected"])
            self.assertEqual(next(value for kind, value in results if kind == "rejected"), 409)
            with Session(bind=engine) as db:
                self.assertEqual(db.scalar(select(func.count()).select_from(Program).where(
                    Program.name.ilike(f"QA Catalog {label.split()[-1]}"))), 1)
        finally:
            with Session(bind=engine) as db:
                rows = db.scalars(select(Program).where(Program.name.ilike(f"%{label.split()[-1]}%"))).all()
                ids = [row.id for row in rows]
                if ids:
                    db.execute(delete(AuthAuditLog).where(AuthAuditLog.target.in_([f"program:{key}" for key in ids])))
                    for row in rows:
                        db.delete(row)
                db.commit()


if __name__ == "__main__":
    unittest.main()
