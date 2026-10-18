"""Closed program catalog on a marked disposable PostgreSQL database."""

import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.api.routes_admin_programs import create_program
from app.database import engine
from app.models.admin_role import AdminRole, SUPER_ADMIN_SLUG
from app.models.program import Program
from app.models.user import RoleEnum, User
from app.schemas.admin_programs import CreateProgramRequest
from tests.postgres_safety import isolated_postgres_enabled


@unittest.skipUnless(isolated_postgres_enabled(), "Requires marked disposable PostgreSQL")
class ProgramCatalogPostgresTests(unittest.TestCase):
    def test_parallel_creation_attempts_are_all_rejected_and_write_nothing(self):
        with Session(bind=engine) as db:
            role = db.scalar(select(AdminRole).where(AdminRole.slug == SUPER_ADMIN_SLUG))
            self.assertIsNotNone(role)
            actor = User(username=f"catalog-reject-qa-{uuid4().hex}@example.test",
                         password_hash="unused", full_name="Catalog Reject QA", role=RoleEnum.admin,
                         admin_role_id=role.id, is_active=False)
            db.add(actor)
            db.commit()
            actor_id = actor.id
            before = db.scalar(select(func.count()).select_from(Program))
        label = f"QA Catalog {uuid4().hex[:12]}"
        barrier = Barrier(2)
        scope = {"type": "http", "method": "POST", "path": "/api/admin/programs",
                 "headers": [], "client": ("127.0.0.1", 0), "query_string": b""}

        def attempt(name):
            with Session(bind=engine) as db:
                actor = db.get(User, actor_id)
                barrier.wait(timeout=10)
                try:
                    create_program(CreateProgramRequest(name=name), Request(scope), db=db, actor=actor)
                    return "created"
                except HTTPException as exc:
                    return exc.status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [future.result(timeout=30) for future in (
                pool.submit(attempt, label), pool.submit(attempt, "  " + label.upper() + "  "))]
        self.assertEqual(results, [403, 403])
        with Session(bind=engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Program)), before)


if __name__ == "__main__":
    unittest.main()
