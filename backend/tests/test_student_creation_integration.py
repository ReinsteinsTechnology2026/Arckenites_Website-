"""Concurrent provisioning against an explicitly marked disposable PostgreSQL DB."""

import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.api.routes_admin_students import create_student
from app.database import engine
from app.models.audit_log import AuthAuditLog
from app.models.program import Program, ProgramEnrollment
from app.models.student import StudentProfile
from app.models.user import User
from app.schemas.admin_students import CreateStudentRequest, CredentialEmailOut
from tests.postgres_safety import isolated_postgres_enabled


@unittest.skipUnless(isolated_postgres_enabled(), "Requires marked disposable PostgreSQL")
class StudentCreationPostgresTests(unittest.TestCase):
    def test_parallel_requests_create_one_identity_and_one_enrollment(self):
        with Session(bind=engine) as db:
            actor = db.scalar(select(User).where(User.username == "admin1@arckenites.com"))
            program = db.scalar(select(Program).where(Program.name == "Institutional Program"))
            self.assertIsNotNone(actor)
            self.assertIsNotNone(program)
            actor_id, program_id = actor.id, program.id
        email = f"qa-{uuid4().hex[:18]}@example.test"
        barrier = Barrier(2)
        scope = {"type": "http", "method": "POST", "path": "/api/admin/students",
                 "headers": [], "client": ("127.0.0.1", 0), "query_string": b""}

        def attempt():
            with Session(bind=engine, autoflush=False) as db:
                actor = db.get(User, actor_id)
                payload = CreateStudentRequest(full_name="Synthetic Student", email=email,
                                               temp_password="SyntheticPassword123", program_id=program_id)
                barrier.wait(timeout=10)
                try:
                    created = create_student(payload, Request(scope), db=db, actor=actor)
                    return "created", created.id
                except HTTPException as exc:
                    return "rejected", exc.status_code

        try:
            with patch("app.api.routes_admin_students.send_student_credentials",
                       return_value=CredentialEmailOut(credential_email_sent=False, detail="QA email disabled")) as send:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = [future.result(timeout=30) for future in [pool.submit(attempt), pool.submit(attempt)]]
                self.assertCountEqual([result[0] for result in results], ["created", "rejected"])
                self.assertEqual(next(value for kind, value in results if kind == "rejected"), 409)
                send.assert_called_once()
            with Session(bind=engine) as db:
                user = db.scalar(select(User).where(User.username == email))
                self.assertIsNotNone(user)
                self.assertEqual(db.scalar(select(func.count()).select_from(ProgramEnrollment).where(
                    ProgramEnrollment.student_id == user.id, ProgramEnrollment.program_id == program_id)), 1)
        finally:
            with Session(bind=engine) as db:
                user = db.scalar(select(User).where(User.username == email))
                if user is not None:
                    db.execute(delete(ProgramEnrollment).where(ProgramEnrollment.student_id == user.id))
                    db.execute(delete(StudentProfile).where(StudentProfile.user_id == user.id))
                    db.execute(delete(AuthAuditLog).where(AuthAuditLog.target == email))
                    db.delete(user)
                db.commit()


if __name__ == "__main__":
    unittest.main()
