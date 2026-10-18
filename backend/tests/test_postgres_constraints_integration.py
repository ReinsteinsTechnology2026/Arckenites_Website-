"""Constraint checks against an explicitly opted-in, disposable local PostgreSQL DB."""

import unittest
import uuid

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import engine
from app.models.user import RoleEnum, User
from postgres_safety import isolated_postgres_enabled


@unittest.skipUnless(isolated_postgres_enabled(), "Requires explicit local QA PostgreSQL opt-in")
class TestPostgresConstraintsIntegration(unittest.TestCase):
    def test_normalized_email_login_index_rejects_case_and_space_duplicate(self):
        suffix = uuid.uuid4().hex[:10]
        with Session(bind=engine) as session:
            first = User(username=f"qa{suffix}@example.test", password_hash="unused",
                         full_name="QA One", role=RoleEnum.student)
            session.add(first)
            session.flush()
            with self.assertRaises(IntegrityError) as raised:
                with session.begin_nested():
                    session.add(User(username=f" QA{suffix}@EXAMPLE.TEST ", password_hash="unused",
                                     full_name="QA Two", role=RoleEnum.student))
                    session.flush()
            self.assertEqual(raised.exception.orig.sqlstate, "23505")
            self.assertEqual(session.scalar(select(User.id).where(User.id == first.id)), first.id)
            session.rollback()

    def test_student_profile_foreign_key_rejects_missing_account(self):
        with Session(bind=engine) as session:
            with self.assertRaises(IntegrityError) as raised:
                session.execute(text("INSERT INTO student_profiles (user_id) VALUES (2147483647)"))
                session.flush()
            self.assertEqual(raised.exception.orig.sqlstate, "23503")
            session.rollback()
