"""Concurrent trainer creation on an explicitly marked disposable PostgreSQL DB."""
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.api.routes_admin_staff import create_staff
from app.database import engine
from app.models.admin_role import AdminRole, SUPER_ADMIN_SLUG
from app.models.audit_log import AuthAuditLog
from app.models.staff import StaffProfile
from app.models.user import RoleEnum, User
from app.schemas.admin_staff import CreateStaffRequest
from app.schemas.admin_students import CredentialEmailOut
from tests.postgres_safety import isolated_postgres_enabled


@unittest.skipUnless(isolated_postgres_enabled(), "Requires marked disposable PostgreSQL")
class TrainerCreationPostgresTests(unittest.TestCase):
    def test_parallel_requests_create_one_trainer_and_one_profile(self):
        with Session(bind=engine) as db:
            role = db.scalar(select(AdminRole).where(AdminRole.slug == SUPER_ADMIN_SLUG))
            self.assertIsNotNone(role)
            actor = User(username=f"trainer-race-qa-{uuid4().hex}@example.test",
                         password_hash="unused", full_name="Trainer Race QA", role=RoleEnum.admin,
                         admin_role_id=role.id, is_active=False)
            db.add(actor)
            db.commit()
            actor_id = actor.id
        email = f"trainer-qa-{uuid4().hex[:18]}@example.test"
        barrier = Barrier(2)
        scope = {"type": "http", "method": "POST", "path": "/api/admin/staff",
                 "headers": [], "client": ("127.0.0.1", 0), "query_string": b""}

        def attempt():
            with Session(bind=engine, autoflush=False) as db:
                actor = db.get(User, actor_id)
                payload = CreateStaffRequest(full_name="Synthetic Trainer", email=email,
                                             temp_password="SyntheticPassword123")
                barrier.wait(timeout=10)
                try:
                    created = create_staff(payload, Request(scope), db=db, actor=actor)
                    return "created", created.id
                except HTTPException as exc:
                    return "rejected", exc.status_code

        try:
            with patch("app.api.routes_admin_staff.send_trainer_credentials",
                       return_value=CredentialEmailOut(credential_email_sent=False, detail="QA mail disabled")) as send:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = [future.result(timeout=30) for future in
                               (pool.submit(attempt), pool.submit(attempt))]
                self.assertCountEqual([kind for kind, _ in results], ["created", "rejected"])
                self.assertEqual(next(value for kind, value in results if kind == "rejected"), 409)
                send.assert_called_once()
            with Session(bind=engine) as db:
                trainer = db.scalar(select(User).where(User.username == email))
                self.assertIsNotNone(trainer)
                self.assertEqual(trainer.role, RoleEnum.staff)
                self.assertEqual(db.scalar(select(func.count()).select_from(StaffProfile).where(
                    StaffProfile.user_id == trainer.id)), 1)
        finally:
            with Session(bind=engine) as db:
                trainer = db.scalar(select(User).where(User.username == email))
                if trainer is not None:
                    db.execute(delete(StaffProfile).where(StaffProfile.user_id == trainer.id))
                    db.execute(delete(AuthAuditLog).where(AuthAuditLog.target == email))
                    db.delete(trainer)
                db.execute(delete(AuthAuditLog).where(AuthAuditLog.user_id == actor_id))
                db.execute(delete(User).where(User.id == actor_id))
                db.commit()


if __name__ == "__main__":
    unittest.main()
